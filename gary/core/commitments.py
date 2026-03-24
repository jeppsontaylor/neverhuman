"""
core/commitments.py — Commitment tracker for GARY

Promotes open_loops that carry obligations (promises, reminders, follow-ups)
into first-class commitments with affective charge, due dates, and
source references.

Commitments influence:
  - Initiative scoring (pending promises boost initiative)
  - Context packing (commitments get priority slots)
  - Affect engine (unmet commitments increase anxiety/frustration)

9/14 reviewers explicitly requested commitment tracking.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("gary.commitments")


@dataclass
class Commitment:
    """A tracked promise or obligation."""
    id: str = ""
    summary: str = ""
    kind: str = "promise"           # promise | reminder | follow_up | thread
    affective_charge: float = 0.3   # how emotionally weighted (0-1)
    priority: float = 0.5
    status: str = "open"            # open | fulfilled | broken | expired
    due_at: Optional[float] = None  # unix timestamp, None = no deadline
    source_refs: list[str] = field(default_factory=list)
    turn_id: str = ""
    last_touched_ts: float = field(default_factory=time.time)


class CommitmentTracker:
    """Manages active commitments in memory with DB persistence."""

    def __init__(self) -> None:
        self._commitments: list[Commitment] = []

    @property
    def active_count(self) -> int:
        return len([c for c in self._commitments if c.status == "open"])

    @property
    def overdue(self) -> list[Commitment]:
        now = time.time()
        return [
            c for c in self._commitments
            if c.status == "open" and c.due_at is not None and c.due_at < now
        ]

    def add(
        self,
        summary: str,
        kind: str = "promise",
        priority: float = 0.5,
        affective_charge: float = 0.3,
        due_at: Optional[float] = None,
        source_refs: Optional[list[str]] = None,
        turn_id: str = "",
    ) -> Commitment:
        """Register a new commitment."""
        from core.mind import new_thought_id
        c = Commitment(
            id=new_thought_id(),
            summary=summary,
            kind=kind,
            priority=priority,
            affective_charge=affective_charge,
            due_at=due_at,
            source_refs=source_refs or [],
            turn_id=turn_id,
        )
        self._commitments.append(c)
        log.info("Commitment added [%s]: %s", c.kind, c.summary[:80])
        return c

    def fulfill(self, commitment_id: str) -> bool:
        """Mark a commitment as fulfilled."""
        for c in self._commitments:
            if c.id == commitment_id:
                c.status = "fulfilled"
                c.last_touched_ts = time.time()
                log.info("Commitment fulfilled: %s", c.summary[:60])
                return True
        return False

    def break_commitment(self, commitment_id: str) -> bool:
        """Mark a commitment as broken (unable to fulfill)."""
        for c in self._commitments:
            if c.id == commitment_id:
                c.status = "broken"
                c.last_touched_ts = time.time()
                log.info("Commitment broken: %s", c.summary[:60])
                return True
        return False

    def get_affect_pressure(self) -> float:
        """Calculate total affective pressure from open commitments.

        Returns a value 0–1 indicating how much commitment pressure
        should influence the affect engine (anxiety, frustration).
        """
        if not self._commitments:
            return 0.0

        open_commitments = [c for c in self._commitments if c.status == "open"]
        if not open_commitments:
            return 0.0

        now = time.time()
        total = 0.0
        for c in open_commitments:
            urgency = 1.0
            if c.due_at is not None:
                remaining = c.due_at - now
                if remaining < 0:
                    urgency = 1.5  # overdue amplification
                elif remaining < 3600:
                    urgency = 1.2  # approaching deadline
            total += c.affective_charge * urgency * c.priority

        # Normalize to 0-1
        return min(1.0, total / max(1, len(open_commitments)))

    def get_initiative_boost(self) -> float:
        """How much should commitments boost initiative scoring.

        Returns 0-1 boost based on overdue/high-priority commitments.
        """
        overdue = self.overdue
        if not overdue:
            return 0.0
        return min(1.0, sum(c.priority * c.affective_charge for c in overdue) / len(overdue))

    def for_context_pack(self, limit: int = 3) -> list[dict]:
        """Return top commitments formatted for the context pack."""
        open_commitments = sorted(
            [c for c in self._commitments if c.status == "open"],
            key=lambda c: c.priority * c.affective_charge,
            reverse=True,
        )
        return [
            {
                "summary": c.summary,
                "kind": c.kind,
                "priority": round(c.priority, 2),
                "charge": round(c.affective_charge, 2),
                "overdue": c.due_at is not None and c.due_at < time.time(),
            }
            for c in open_commitments[:limit]
        ]

    async def sync_to_db(self, conn) -> int:
        """Sync commitments to the open_loops table with commitment extensions."""
        count = 0
        for c in self._commitments:
            if c.status == "open":
                try:
                    await conn.execute(
                        """
                        INSERT INTO open_loops (summary, priority, status, affective_charge, source_refs, last_touched_at, turn_id)
                        VALUES ($1, $2, $3, $4, $5::uuid[], $6, $7)
                        """,
                        c.summary, c.priority, c.status,
                        c.affective_charge, c.source_refs,
                        None,  # let DB default
                        c.turn_id,
                    )
                    count += 1
                except Exception as exc:
                    log.warning("Failed to sync commitment: %s", exc)
        return count
