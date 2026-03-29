"""
core/resource_arbiter.py — Resource arbitration: live conversation always wins

Controls background resource usage to prevent latency degradation:
  - Forge test runs: pause if user becomes active
  - Replay harness: low priority, interruptible
  - Code indexing: background, throttled
  - Mind sidecar: preempted instantly on onset
  - Circuit breaker: if TTFT p95 degrades, pause ALL background work
"""
from __future__ import annotations

import logging
import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

log = logging.getLogger("gary.resource_arbiter")


class ResourcePriority(int, Enum):
    CRITICAL = 0     # Live conversation (reflex path)
    HIGH     = 1     # User-initiated tasks
    NORMAL   = 2     # Mind sidecar
    LOW      = 3     # Forge tests, replay harness
    IDLE     = 4     # Code indexing, atlas refresh


class ResourceKind(str, Enum):
    REFLEX      = "reflex"          # Live conversation
    MIND        = "mind"            # Mind sidecar
    FORGE       = "forge"           # Self-edit testing
    REPLAY      = "replay"          # Replay harness
    INDEXING    = "indexing"         # Code atlas refresh
    DISCOVERY   = "discovery"       # Research pipeline


@dataclass
class ResourceClaim:
    """A claim on system resources by a background task."""
    kind: ResourceKind
    priority: ResourcePriority
    task_id: str
    started_at: float = field(default_factory=time.monotonic)
    paused: bool = False
    paused_at: Optional[float] = None

    def pause(self) -> None:
        if not self.paused:
            self.paused = True
            self.paused_at = time.monotonic()
            log.info(f"⏸️  Paused {self.kind.value} task {self.task_id}")

    def resume(self) -> None:
        if self.paused:
            self.paused = False
            self.paused_at = None
            log.info(f"▶️  Resumed {self.kind.value} task {self.task_id}")


@dataclass
class TTFTMetrics:
    """Time-to-first-token metrics for circuit breaker."""
    samples: list[float] = field(default_factory=list)
    max_samples: int = 50
    threshold_ms: float = 2000.0   # p95 threshold for circuit breaker

    def record(self, ttft_ms: float) -> None:
        self.samples.append(ttft_ms)
        if len(self.samples) > self.max_samples:
            self.samples = self.samples[-self.max_samples:]

    @property
    def p95(self) -> float:
        if not self.samples:
            return 0.0
        sorted_samples = sorted(self.samples)
        idx = int(len(sorted_samples) * 0.95)
        return sorted_samples[min(idx, len(sorted_samples) - 1)]

    @property
    def is_degraded(self) -> bool:
        return len(self.samples) >= 5 and self.p95 > self.threshold_ms


class ResourceArbiter:
    """Live conversation always wins. Background work is preemptible."""

    def __init__(self):
        self._claims: dict[str, ResourceClaim] = {}
        self.ttft_metrics = TTFTMetrics()
        self._circuit_broken = False
        self._arbiter_bin = os.getenv("GARY_RESOURCE_ARBITER_BIN", "")
        self._rust_state: dict | None = None

    def _apply_rust(self, operation: dict) -> dict | None:
        if not self._arbiter_bin:
            return None
        payload = {"operation": operation}
        if self._rust_state is not None:
            payload["state"] = self._rust_state
        try:
            res = subprocess.run(
                [self._arbiter_bin],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                check=True,
                timeout=0.35,
            )
            out = json.loads(res.stdout)
            self._rust_state = out.get("state")
            return out
        except Exception:
            return None

    def _hydrate_from_rust_state(self) -> None:
        if not self._rust_state:
            return
        self._circuit_broken = bool(self._rust_state.get("circuit_broken", False))
        self.ttft_metrics.samples = list(self._rust_state.get("ttft_samples", []))
        self.ttft_metrics.max_samples = int(self._rust_state.get("max_samples", 50))
        self.ttft_metrics.threshold_ms = float(self._rust_state.get("threshold_ms", 2000.0))
        claims: dict[str, ResourceClaim] = {}
        for task_id, obj in self._rust_state.get("claims", {}).items():
            claims[task_id] = ResourceClaim(
                kind=ResourceKind(obj["kind"]),
                priority=ResourcePriority[obj["priority"].upper()],
                task_id=task_id,
                paused=bool(obj.get("paused", False)),
            )
        self._claims = claims

    @property
    def circuit_broken(self) -> bool:
        """True if TTFT p95 has degraded — all background work paused."""
        if self._arbiter_bin:
            out = self._apply_rust({"op": "status"})
            if out is not None:
                self._hydrate_from_rust_state()
        return self._circuit_broken

    def register_claim(
        self, kind: ResourceKind, task_id: str, priority: ResourcePriority,
    ) -> ResourceClaim:
        """Register a resource claim for a background task."""
        out = self._apply_rust(
            {
                "op": "register_claim",
                "kind": kind.value,
                "task_id": task_id,
                "priority": priority.name.lower(),
            }
        )
        if out is not None:
            self._hydrate_from_rust_state()
            return self._claims[task_id]

        claim = ResourceClaim(kind=kind, priority=priority, task_id=task_id)
        self._claims[task_id] = claim

        # If circuit is broken, immediately pause non-critical
        if self._circuit_broken and priority.value > ResourcePriority.CRITICAL.value:
            claim.pause()

        return claim

    def release_claim(self, task_id: str) -> None:
        """Release a resource claim when task completes."""
        out = self._apply_rust({"op": "release_claim", "task_id": task_id})
        if out is not None:
            self._hydrate_from_rust_state()
            return
        self._claims.pop(task_id, None)

    def on_user_active(self) -> list[str]:
        """User became active — pause lower-priority tasks.

        Returns list of paused task IDs.
        """
        out = self._apply_rust({"op": "on_user_active"})
        if out is not None:
            self._hydrate_from_rust_state()
            return list(out.get("paused_task_ids", []))

        paused = []
        for task_id, claim in self._claims.items():
            if claim.priority.value > ResourcePriority.HIGH.value and not claim.paused:
                claim.pause()
                paused.append(task_id)
        return paused

    def on_user_idle(self) -> list[str]:
        """User became idle — resume paused tasks.

        Returns list of resumed task IDs.
        """
        out = self._apply_rust({"op": "on_user_idle"})
        if out is not None:
            self._hydrate_from_rust_state()
            return list(out.get("resumed_task_ids", []))

        if self._circuit_broken:
            return []  # don't resume while circuit is broken

        resumed = []
        for task_id, claim in self._claims.items():
            if claim.paused:
                claim.resume()
                resumed.append(task_id)
        return resumed

    def on_onset(self) -> list[str]:
        """Mic onset detected — immediately preempt mind + all background.

        Returns list of paused task IDs.
        """
        out = self._apply_rust({"op": "on_onset"})
        if out is not None:
            self._hydrate_from_rust_state()
            return list(out.get("paused_task_ids", []))

        paused = []
        for task_id, claim in self._claims.items():
            if claim.kind != ResourceKind.REFLEX and not claim.paused:
                claim.pause()
                paused.append(task_id)
        return paused

    def record_ttft(self, ttft_ms: float) -> None:
        """Record a time-to-first-token measurement."""
        out = self._apply_rust({"op": "record_ttft", "ttft_ms": ttft_ms})
        if out is not None:
            self._hydrate_from_rust_state()
            return

        self.ttft_metrics.record(ttft_ms)
        was_broken = self._circuit_broken
        self._circuit_broken = self.ttft_metrics.is_degraded

        if self._circuit_broken and not was_broken:
            log.warning(
                f"🔴 Circuit breaker TRIPPED: TTFT p95={self.ttft_metrics.p95:.0f}ms "
                f"(threshold={self.ttft_metrics.threshold_ms:.0f}ms)"
            )
            # Pause all non-critical
            for claim in self._claims.values():
                if claim.priority.value > ResourcePriority.CRITICAL.value:
                    claim.pause()
        elif not self._circuit_broken and was_broken:
            log.info("🟢 Circuit breaker CLEARED — TTFT p95 recovered")
            self.on_user_idle()  # resume background

    def should_allow(self, kind: ResourceKind) -> bool:
        """Quick check: should this kind of work be allowed right now?"""
        out = self._apply_rust({"op": "should_allow", "kind": kind.value})
        if out is not None:
            self._hydrate_from_rust_state()
            allow = out.get("allow")
            return bool(allow) if allow is not None else True

        if self._circuit_broken and kind != ResourceKind.REFLEX:
            return False
        return True

    def status(self) -> dict:
        out = self._apply_rust({"op": "status"})
        if out is not None:
            self._hydrate_from_rust_state()
            status = out.get("status")
            if isinstance(status, dict):
                return status

        return {
            "circuit_broken": self._circuit_broken,
            "ttft_p95_ms": round(self.ttft_metrics.p95, 1),
            "active_claims": len(self._claims),
            "paused_claims": sum(1 for c in self._claims.values() if c.paused),
            "claims": {
                tid: {"kind": c.kind.value, "priority": c.priority.value, "paused": c.paused}
                for tid, c in self._claims.items()
            },
        }
