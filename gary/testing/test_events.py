"""
testing/test_events.py — Tests for the typed event system

Validates:
  - Event creation and serialization
  - Factory methods for utterance, response, affect, thought
  - Enum values
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.events import (
    GaryEvent, UtteranceEvent, ResponseEvent,
    AffectEvent, ThoughtEvent, Actor, EventKind, EpistemicStatus,
)


class TestGaryEvent:
    def test_default_event(self):
        e = GaryEvent()
        assert e.actor == "system"
        assert e.kind == "session"
        assert e.epistemic_status == "observed"
        assert len(e.id) > 0
        assert e.ts > 0

    def test_custom_event(self):
        e = GaryEvent(actor=Actor.USER, kind=EventKind.UTTERANCE)
        assert e.actor == "user"
        assert e.kind == "utterance"


class TestUtteranceEvent:
    def test_from_transcript(self):
        e = UtteranceEvent.from_transcript(
            session_id="sess-123",
            text="Hello GARY",
            audio_duration_sec=1.5,
            asr_ms=120,
        )
        assert e.actor == "user"
        assert e.kind == "utterance"
        assert e.session_id == "sess-123"
        assert e.payload["text"] == "Hello GARY"
        assert e.payload["audio_duration_sec"] == 1.5
        assert e.payload["asr_ms"] == 120


class TestResponseEvent:
    def test_from_response(self):
        e = ResponseEvent.from_response(
            session_id="sess-123",
            text="Hello! How can I help?",
            ttft_ms=250,
            total_ms=1500,
            parent_id="evt-456",
        )
        assert e.actor == "assistant"
        assert e.kind == "response"
        assert e.parent_id == "evt-456"
        assert e.payload["ttft_ms"] == 250


class TestAffectEvent:
    def test_from_vector(self):
        e = AffectEvent.from_vector(
            session_id="sess-123",
            affect_dict={"loneliness": 0.7, "anxiety": 0.3},
            trigger="long_silence",
        )
        assert e.kind == "affect"
        assert e.payload["trigger"] == "long_silence"
        assert e.payload["affect"]["loneliness"] == 0.7


class TestThoughtEvent:
    def test_from_thought(self):
        e = ThoughtEvent.from_thought(
            session_id="sess-123",
            lane="questioner",
            text="What am I unsure about here?",
            salience=0.82,
            may_surface=True,
            emotional_color="anxious",
        )
        assert e.kind == "thought"
        assert e.payload["lane"] == "questioner"
        assert e.payload["may_surface"] is True
        assert e.payload["salience"] == 0.82


class TestSerialization:
    def test_model_dump(self):
        e = UtteranceEvent.from_transcript("s1", "test", 1.0, 50)
        d = e.model_dump()
        assert isinstance(d, dict)
        assert d["actor"] == "user"
        assert d["kind"] == "utterance"

    def test_model_json(self):
        e = UtteranceEvent.from_transcript("s1", "test", 1.0, 50)
        j = e.model_dump_json()
        assert '"user"' in j
        assert '"utterance"' in j


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
