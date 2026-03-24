"""
memory/retrieval_audit.py — Retrieval audit trail

Logs what context was compiled into each turn's context pack.
Enables the question: "what was in context this turn?"
and later: "what context actually helped?"
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("gary.retrieval_audit")


async def log_retrieval_audit(
    conn,
    *,
    pack_hash: str,
    turn_id: str = "",
    retrieved_ids: list[str] | None = None,
    slot_counts: dict | None = None,
    used_in_answer: bool | None = None,
) -> None:
    """Insert a retrieval audit row.

    Args:
        conn: asyncpg connection
        pack_hash: sha256 hash of the compiled context pack
        turn_id: optional turn identifier
        retrieved_ids: list of UUID strings for claims/loops/memories used
        slot_counts: dict like {"open_loops": 2, "claims": 3}
        used_in_answer: whether the answer referenced the retrieved context
    """
    import json

    ids = retrieved_ids or []
    counts = slot_counts or {}

    try:
        await conn.execute(
            """
            INSERT INTO retrieval_log (pack_hash, turn_id, retrieved_ids, slot_counts, used_in_answer)
            VALUES ($1, $2, $3::uuid[], $4::jsonb, $5)
            """,
            pack_hash,
            turn_id,
            ids,
            json.dumps(counts),
            used_in_answer,
        )
    except Exception as exc:
        log.warning("Failed to log retrieval audit: %s", exc)
