"""
core/session_checkpoint.py — Session state checkpointing for blue-green swaps

Stores minimal session state in DB so candidate B can restore context
on reconnect. Without this, a swap feels like mini-amnesia.

Checkpoint includes:
  - Session history window
  - Floor state snapshot
  - Pending turn state
  - Affect snapshot
  - Session overlay settings
  - Voice/runtime prefs
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

log = logging.getLogger("gary.session_checkpoint")


@dataclass
class SessionCheckpoint:
    """Minimal checkpoint for session continuity across swaps."""
    session_id: str
    created_at: float = field(default_factory=time.monotonic)

    # Conversation state
    history_window: list[dict[str, str]] = field(default_factory=list)  # last N turns
    max_history: int = 20

    # Floor state
    floor_owner: str = "NONE"
    engagement: str = "IDLE"
    system_mode: str = "NORMAL"
    pending_turn_id: Optional[str] = None

    # Affect state
    affect_snapshot: dict[str, float] = field(default_factory=dict)
    drive_snapshot: dict[str, float] = field(default_factory=dict)

    # Session overlay (temporary settings)
    session_overlays: dict[str, Any] = field(default_factory=dict)

    # Preferences
    voice_setting: str = "default"
    mind_disclosure_level: str = "quest_cards"
    response_style: str = "balanced"

    def add_turn(self, role: str, content: str) -> None:
        """Add a turn to the history window."""
        self.history_window.append({
            "role": role,
            "content": content,
            "timestamp": str(time.monotonic()),
        })
        # Trim to max
        if len(self.history_window) > self.max_history:
            self.history_window = self.history_window[-self.max_history:]

    def snapshot_floor(
        self,
        floor_owner: str,
        engagement: str,
        system_mode: str,
        pending_turn_id: Optional[str] = None,
    ) -> None:
        """Take a snapshot of the current floor state."""
        self.floor_owner = floor_owner
        self.engagement = engagement
        self.system_mode = system_mode
        self.pending_turn_id = pending_turn_id

    def snapshot_affect(self, affect: dict[str, float], drives: dict[str, float]) -> None:
        """Take a snapshot of the current affect and drive state."""
        self.affect_snapshot = dict(affect)
        self.drive_snapshot = dict(drives)

    def set_overlay(self, key: str, value: Any) -> None:
        """Set a session overlay setting."""
        self.session_overlays[key] = value

    def to_json(self) -> str:
        """Serialize to JSON for DB storage."""
        return json.dumps(asdict(self), default=str)

    @classmethod
    def from_json(cls, data: str) -> "SessionCheckpoint":
        """Deserialize from JSON."""
        d = json.loads(data)
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})

    def restore_summary(self) -> str:
        """Summary of what's being restored for logging."""
        return (
            f"Session {self.session_id}: "
            f"{len(self.history_window)} turns, "
            f"floor={self.floor_owner}/{self.engagement}, "
            f"mode={self.system_mode}, "
            f"overlays={len(self.session_overlays)}"
        )


class CheckpointStore:
    """In-memory checkpoint store. In production, backed by DB."""

    def __init__(self):
        self._store: dict[str, SessionCheckpoint] = {}

    def save(self, checkpoint: SessionCheckpoint) -> None:
        """Save a checkpoint."""
        self._store[checkpoint.session_id] = checkpoint
        log.debug(f"✅ Checkpoint saved: {checkpoint.restore_summary()}")

    def load(self, session_id: str) -> Optional[SessionCheckpoint]:
        """Load a checkpoint."""
        cp = self._store.get(session_id)
        if cp:
            log.debug(f"📦 Checkpoint loaded: {cp.restore_summary()}")
        return cp

    def delete(self, session_id: str) -> None:
        """Delete a checkpoint after successful restore."""
        self._store.pop(session_id, None)

    def list_sessions(self) -> list[str]:
        """List all checkpointed session IDs."""
        return list(self._store.keys())

    def cleanup_stale(self, max_age_sec: float = 3600.0) -> int:
        """Remove checkpoints older than max_age_sec."""
        now = time.monotonic()
        stale = [
            sid for sid, cp in self._store.items()
            if (now - cp.created_at) > max_age_sec
        ]
        for sid in stale:
            del self._store[sid]
        if stale:
            log.info(f"🧹 Cleaned up {len(stale)} stale checkpoints")
        return len(stale)
