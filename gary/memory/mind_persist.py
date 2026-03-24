"""
memory/mind_persist.py — Persist structured mind pulses (schema v1) to thoughts table

Requires Postgres + asyncpg + applied memory/schema.sql.
Enable from server with GARY_PERSIST_MIND=1 (only writes when JSON pulse parses).
"""
from __future__ import annotations

import json
from dataclasses import asdict

from core.mind_pulse import MindPulse


def phase_to_pulse_type(phase: str) -> str:
    return {
        "reflecting": "reflection",
        "brainstorming": "brainstorm",
        "dreaming": "dream",
    }.get(phase, "reflection")


async def persist_structured_thought(
    conn,
    *,
    thought_id: str,
    session_id: str,
    pulse: MindPulse,
    phase: str,
    salience: float,
    may_surface: bool,
) -> None:
    """Insert one row into thoughts with full pulse JSON in content."""
    content = json.dumps(asdict(pulse), default=str)
    pulse_type = phase_to_pulse_type(phase)
    await conn.execute(
        """
        INSERT INTO thoughts (
            id, session_id, lane, pulse_type, content, salience, may_surface,
            emotional_color, epistemic_status
        )
        VALUES (
            $1::uuid, $2, $3, $4, $5, $6, $7, 'neutral', 'inferred'
        )
        """,
        thought_id,
        session_id,
        "structured_json",
        pulse_type,
        content,
        float(salience),
        may_surface,
    )
