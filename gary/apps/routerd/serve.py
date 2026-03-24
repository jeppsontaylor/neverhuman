"""
apps/routerd/serve.py — Immutable front-door WebSocket proxy

Tiny process that:
  - Accepts WS on port 7861 (public, stable)
  - Proxies to active reflex slot (A or B)
  - Health-checks both slots
  - A/B traffic switching (drain-based)
  - Rollback command
  - Kill switch

IMMUTABLE: This file is in the immutable tier (edit_policies.yml).
Candidates cannot edit routerd.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

log = logging.getLogger("gary.routerd")

DEFAULT_PORT = 7861
HEALTH_CHECK_INTERVAL = 5.0   # seconds
ROLLBACK_WINDOW = 300.0        # 5 min warm rollback period


class SlotStatus(str, Enum):
    HEALTHY   = "healthy"
    UNHEALTHY = "unhealthy"
    DRAINING  = "draining"
    STANDBY   = "standby"
    OFFLINE   = "offline"


@dataclass
class Slot:
    """A reflex slot (A or B)."""
    name: str                   # "A" or "B"
    host: str = "127.0.0.1"
    port: int = 7862
    status: SlotStatus = SlotStatus.OFFLINE
    active_sessions: int = 0
    api_version: str = "4.1"
    ui_asset_version: str = "1.0"
    last_health_check: float = 0.0
    promoted_at: Optional[float] = None

    @property
    def is_available(self) -> bool:
        return self.status in (SlotStatus.HEALTHY, SlotStatus.DRAINING)

    @property
    def ws_url(self) -> str:
        return f"ws://{self.host}:{self.port}/ws"

    @property
    def health_url(self) -> str:
        return f"http://{self.host}:{self.port}/health"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "status": self.status.value,
            "active_sessions": self.active_sessions,
            "api_version": self.api_version,
            "ui_asset_version": self.ui_asset_version,
            "promoted_at": self.promoted_at,
        }


@dataclass
class ControlPlane:
    """Blue-green control plane state."""
    slot_a: Slot = field(default_factory=lambda: Slot(name="A", port=7862))
    slot_b: Slot = field(default_factory=lambda: Slot(name="B", port=7866))
    active_slot: str = "A"      # "A" or "B"
    swap_in_progress: bool = False
    last_swap_at: Optional[float] = None

    @property
    def active(self) -> Slot:
        return self.slot_a if self.active_slot == "A" else self.slot_b

    @property
    def standby(self) -> Slot:
        return self.slot_b if self.active_slot == "A" else self.slot_a

    def can_swap(self, supervisor_floor_owner: str, supervisor_engagement: str) -> bool:
        """Check if a swap is safe right now.

        RULE: Switch ONLY when floor_owner == NONE and engagement ∈ {IDLE, ENGAGED}.
        NEVER mid-turn.
        """
        if self.swap_in_progress:
            return False
        if supervisor_floor_owner != "NONE":
            return False
        if supervisor_engagement not in ("IDLE", "ENGAGED"):
            return False
        if not self.standby.is_available:
            return False
        return True

    def check_compatibility(self) -> tuple[bool, Optional[str]]:
        """Check if standby is protocol-compatible with active.

        Candidate B must not promote if it expects an interface version
        the warm daemons don't implement.
        """
        if self.standby.api_version != self.active.api_version:
            return False, f"API version mismatch: active={self.active.api_version}, standby={self.standby.api_version}"
        return True, None

    def initiate_swap(self) -> bool:
        """Begin drain-based swap."""
        if self.swap_in_progress:
            return False

        self.swap_in_progress = True
        # Mark active as draining
        self.active.status = SlotStatus.DRAINING
        log.info(f"⚡ Initiating swap: {self.active_slot} → {self.standby.name}")
        return True

    def complete_swap(self) -> None:
        """Complete the swap after drain."""
        old_active = self.active_slot

        # Swap active
        if self.active_slot == "A":
            self.active_slot = "B"
        else:
            self.active_slot = "A"

        # Update statuses
        self.active.status = SlotStatus.HEALTHY
        self.active.promoted_at = time.monotonic()
        self.standby.status = SlotStatus.STANDBY

        self.swap_in_progress = False
        self.last_swap_at = time.monotonic()
        log.info(f"✅ Swap complete: {old_active} → {self.active_slot}")

    def rollback(self) -> bool:
        """Rollback to previous slot if within the rollback window."""
        if self.last_swap_at is None:
            return False
        if (time.monotonic() - self.last_swap_at) > ROLLBACK_WINDOW:
            log.warning("Rollback window expired")
            return False
        if not self.standby.is_available:
            return False

        log.info(f"🔄 Rolling back from {self.active_slot} to {self.standby.name}")
        self.initiate_swap()
        self.complete_swap()
        return True

    def is_drained(self) -> bool:
        """Check if the draining slot has no more sessions."""
        draining = self.slot_a if self.slot_a.status == SlotStatus.DRAINING else (
            self.slot_b if self.slot_b.status == SlotStatus.DRAINING else None
        )
        if draining is None:
            return True
        return draining.active_sessions == 0

    def status_report(self) -> dict:
        return {
            "active_slot": self.active_slot,
            "slot_a": self.slot_a.to_dict(),
            "slot_b": self.slot_b.to_dict(),
            "swap_in_progress": self.swap_in_progress,
            "can_rollback": (
                self.last_swap_at is not None
                and (time.monotonic() - self.last_swap_at) < ROLLBACK_WINDOW
            ),
        }


@dataclass
class DaemonRegistry:
    """Registry of all persistent daemons with API version contracts."""
    daemons: dict[str, "DaemonInfo"] = field(default_factory=dict)

    def register(self, name: str, port: int, api_version: str, capabilities: list[str]) -> None:
        self.daemons[name] = DaemonInfo(
            name=name, port=port,
            api_version=api_version, capabilities=capabilities,
        )

    def check_candidate_compat(self, required: dict[str, str]) -> tuple[bool, list[str]]:
        """Check if candidate's required daemon versions are satisfied."""
        errors = []
        for daemon_name, required_version in required.items():
            info = self.daemons.get(daemon_name)
            if info is None:
                errors.append(f"Daemon {daemon_name} not registered")
            elif info.api_version != required_version:
                errors.append(
                    f"Daemon {daemon_name}: has {info.api_version}, "
                    f"candidate needs {required_version}"
                )
        return len(errors) == 0, errors


@dataclass
class DaemonInfo:
    name: str
    port: int
    api_version: str
    capabilities: list[str] = field(default_factory=list)
    healthy: bool = True
