"""testing/test_mind_persist.py — Structured thought INSERT (mocked conn)."""
import asyncio
import sys
import os
import uuid
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.mind_pulse import MindPulse, ThoughtFrame
from memory.mind_persist import persist_structured_thought, phase_to_pulse_type


def test_phase_to_pulse_type():
    assert phase_to_pulse_type("reflecting") == "reflection"
    assert phase_to_pulse_type("dreaming") == "dream"
    assert phase_to_pulse_type("unknown") == "reflection"


def test_persist_calls_execute():
    async def _run():
        conn = AsyncMock()
        conn.execute = AsyncMock(return_value="INSERT 1")
        tid = str(uuid.uuid4())
        pulse = MindPulse(
            schema_version=1,
            inner_voice=["a"],
            frames=[ThoughtFrame(kind="question", text="Why?", salience=0.8)],
            initiative_candidate=None,
        )
        await persist_structured_thought(
            conn,
            thought_id=tid,
            session_id="sess1",
            pulse=pulse,
            phase="reflecting",
            salience=0.7,
            may_surface=False,
        )
        conn.execute.assert_awaited_once()
        args = conn.execute.await_args[0]
        assert args[1] == tid

    asyncio.run(_run())
