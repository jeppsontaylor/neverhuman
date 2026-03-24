"""
core/question_ledger.py — Curiosity ledger for GARY

Manages questions that GARY is curious about. Questions can be
user-posed (the user asked something we couldn't fully answer) or
self-generated (the mind daemon notices a gap in understanding).

Questions drive the cognitive agenda: they feed into context packs,
influence retrieval ranking, and can be resolved by linking to claims.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("gary.question_ledger")


@dataclass
class Question:
    """A question GARY is tracking."""
    id: str = ""
    scope: str = "user"           # user | self | world
    kind: str = "unresolved"       # unresolved | exploring | answered | abandoned
    text: str = ""
    status: str = "open"           # open | answered | abandoned
    salience: float = 0.5
    may_surface: bool = False      # can this be asked aloud?
    source_event_ids: list[str] = field(default_factory=list)
    answer_claim_id: Optional[str] = None


class QuestionLedger:
    """In-memory + DB-backed curiosity ledger."""

    def __init__(self) -> None:
        self._open_questions: list[Question] = []

    @property
    def open_count(self) -> int:
        return len(self._open_questions)

    @property
    def top_question(self) -> Optional[Question]:
        if not self._open_questions:
            return None
        return max(self._open_questions, key=lambda q: q.salience)

    def add_question(
        self,
        text: str,
        scope: str = "user",
        salience: float = 0.5,
        may_surface: bool = False,
        source_event_ids: Optional[list[str]] = None,
    ) -> Question:
        """Register a new question."""
        from core.mind import new_thought_id
        q = Question(
            id=new_thought_id(),
            scope=scope,
            text=text,
            salience=salience,
            may_surface=may_surface,
            source_event_ids=source_event_ids or [],
        )
        self._open_questions.append(q)
        log.info("Question added [%s]: %s", q.scope, q.text[:80])
        return q

    def resolve(self, question_id: str, answer_claim_id: str) -> bool:
        """Mark a question as answered by linking to a claim."""
        for q in self._open_questions:
            if q.id == question_id:
                q.status = "answered"
                q.kind = "answered"
                q.answer_claim_id = answer_claim_id
                self._open_questions.remove(q)
                log.info("Question resolved [%s]: %s", q.id, q.text[:60])
                return True
        return False

    def abandon(self, question_id: str) -> bool:
        """Mark a question as abandoned."""
        for q in self._open_questions:
            if q.id == question_id:
                q.status = "abandoned"
                q.kind = "abandoned"
                self._open_questions.remove(q)
                log.info("Question abandoned [%s]: %s", q.id, q.text[:60])
                return True
        return False

    def boost_salience(self, question_id: str, delta: float = 0.1) -> None:
        """Raise salience when a question is referenced again."""
        for q in self._open_questions:
            if q.id == question_id:
                q.salience = min(1.0, q.salience + delta)
                break

    def decay_salience(self, factor: float = 0.98) -> None:
        """Slowly decay all open question salience (called per mind cycle)."""
        for q in self._open_questions:
            q.salience *= factor
            if q.salience < 0.05:
                q.status = "abandoned"
                q.kind = "abandoned"
        self._open_questions = [
            q for q in self._open_questions if q.status == "open"
        ]

    def get_surfaceable(self, min_salience: float = 0.6) -> list[Question]:
        """Questions that are salient enough to ask the user."""
        return [
            q for q in self._open_questions
            if q.may_surface and q.salience >= min_salience
        ]

    async def persist(self, conn) -> int:
        """Persist open questions to the DB questions table. Returns count persisted."""
        import json
        count = 0
        for q in self._open_questions:
            try:
                await conn.execute(
                    """
                    INSERT INTO questions (id, scope, kind, text, status, salience, may_surface, source_event_ids)
                    VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8::uuid[])
                    ON CONFLICT (id) DO UPDATE SET
                        status = EXCLUDED.status,
                        salience = EXCLUDED.salience,
                        kind = EXCLUDED.kind
                    """,
                    q.id, q.scope, q.kind, q.text, q.status,
                    q.salience, q.may_surface, q.source_event_ids,
                )
                count += 1
            except Exception as exc:
                log.warning("Failed to persist question %s: %s", q.id, exc)
        return count

    async def load_from_db(self, conn, limit: int = 20) -> int:
        """Load open questions from DB into memory. Returns count loaded."""
        try:
            rows = await conn.fetch(
                "SELECT id, scope, kind, text, status, salience, may_surface, "
                "source_event_ids, answer_claim_id "
                "FROM questions WHERE status = 'open' "
                "ORDER BY salience DESC LIMIT $1",
                limit,
            )
            self._open_questions = [
                Question(
                    id=str(r["id"]),
                    scope=r["scope"],
                    kind=r["kind"],
                    text=r["text"],
                    status=r["status"],
                    salience=r["salience"],
                    may_surface=r["may_surface"],
                    source_event_ids=[str(i) for i in (r["source_event_ids"] or [])],
                    answer_claim_id=str(r["answer_claim_id"]) if r["answer_claim_id"] else None,
                )
                for r in rows
            ]
            return len(self._open_questions)
        except Exception as exc:
            log.warning("Failed to load questions from DB: %s", exc)
            return 0
