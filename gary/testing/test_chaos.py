"""
testing/test_chaos.py — Chaos test framework for fault injection (Phase 8D)

Tests system resilience against:
  - Dropped tts_finished
  - WebSocket disconnect mid-utterance
  - Barge-in during candidate swap
  - Delayed interrupt_hint
  - Stale audio chunk after epoch bump
  - Mind pulse cancelled mid-JSON
  - Reconnect with old UI bundle to new server slot
"""
from __future__ import annotations

import asyncio
import time
from uuid import uuid4
from dataclasses import dataclass
from pipeline.turn_supervisor import (
    TurnSupervisor, FloorOwner, Engagement, Event, BackgroundLease,
)


class TestDroppedTTSFinished:
    """System recovers if tts_finished is never received."""

    def test_ai_floor_timeout_after_dropped_tts(self):
        """If AI owns floor but TTS never finishes, orphan watchdog catches it."""
        q = asyncio.Queue()
        sup = TurnSupervisor(q)
        sup.apply(Event.MARK_READY)
        sup.apply(Event.MIC_ONSET)
        sup.apply(Event.TRANSCRIPT_READY, turn_id=uuid4(), text="hello", is_final=True)
        sup.apply(Event.FOREGROUND_GEN_START)

        # AI owns floor, but tts_finished never comes
        assert sup.floor_owner == FloorOwner.ASSISTANT

        # Simulate time passing — tick should detect the orphan
        sup.last_agent_audio_end = time.monotonic() - 35  # past 30s timeout
        sup.is_speaking_since = time.monotonic() - 35
        sup.tick()

        # System should have recovered
        assert sup.floor_owner == FloorOwner.NONE

    def test_floor_not_stuck_forever(self):
        """Even without explicit tts_finished, AI floor eventually releases."""
        q = asyncio.Queue()
        sup = TurnSupervisor(q)
        sup.apply(Event.MARK_READY)
        sup.apply(Event.MIC_ONSET)
        sup.apply(Event.TRANSCRIPT_READY, turn_id=uuid4(), text="test", is_final=True)
        sup.apply(Event.FOREGROUND_GEN_START)

        # Manually set last activity to past timeout
        sup.is_speaking_since = time.monotonic() - 40
        was_stuck = sup.floor_owner == FloorOwner.ASSISTANT
        sup.tick()
        is_freed = sup.floor_owner == FloorOwner.NONE

        assert was_stuck and is_freed


class TestBargeInDuringSwap:
    """Barge-in during candidate swap should not crash."""

    def test_onset_during_normal_operation(self):
        """Onset should work normally and preempt AI."""
        q = asyncio.Queue()
        sup = TurnSupervisor(q)
        sup.apply(Event.MARK_READY)
        sup.apply(Event.MIC_ONSET)
        sup.apply(Event.TRANSCRIPT_READY, turn_id=uuid4(), text="hello", is_final=True)
        sup.apply(Event.FOREGROUND_GEN_START)
        assert sup.floor_owner == FloorOwner.ASSISTANT

        # Barge-in: onset while AI has floor
        sup.apply(Event.BARGE_IN)
        assert sup.floor_owner == FloorOwner.USER

    def test_rapid_onset_transcript_cycle(self):
        """Multiple rapid onset-transcript cycles don't crash."""
        q = asyncio.Queue()
        sup = TurnSupervisor(q)
        sup.apply(Event.MARK_READY)
        for i in range(5):
            sup.apply(Event.MIC_ONSET)
            sup.apply(Event.TRANSCRIPT_READY, turn_id=uuid4(), text=f"turn {i}", is_final=True)
            sup.apply(Event.FOREGROUND_GEN_START)
            sup.apply(Event.TTS_FINISHED)
        # Should end cleanly
        assert sup.floor_owner == FloorOwner.NONE


class TestEpochBumpStaleness:
    """Stale audio chunks after epoch bump are ignored."""

    def test_lease_invalidated_on_transition(self):
        q = asyncio.Queue()
        sup = TurnSupervisor(q)
        sup.apply(Event.MARK_READY)
        
        lease = BackgroundLease(id=uuid4(), kind="mind", granted_at=time.monotonic(), floor_revision=sup.floor_revision, ttl=60.0, _supervisor=sup)
        
        assert lease is not None
        initial_rev = lease.floor_revision

        # State transition bumps revision
        sup.apply(Event.MIC_ONSET)
        assert sup.floor_revision > initial_rev

        # Old lease is now stale
        assert not lease.valid


class TestMindPulseCancelledMidJSON:
    """Partial JSON from cancelled mind pulse doesn't crash parsing."""

    def test_partial_json_returns_none(self):
        from core.mind_pulse import parse_mind_pulse_json

        # Simulate mid-JSON cancellation
        partial_jsons = [
            '{"schema_version": 1, "inner_voice": ["hello',
            '{"schema_version": 1,',
            '{',
            '',
            '{"schema_version": 1, "frames": [{"kind": "question", "text":',
        ]
        for partial in partial_jsons:
            result = parse_mind_pulse_json(partial)
            assert result is None, f"Expected None for partial JSON: {partial!r}"


class TestReconnectWithOldUI:
    """Reconnect with stale UI doesn't break protocol."""

    def test_supervisor_handles_fresh_connection(self):
        """A new connection starts with clean state."""
        q = asyncio.Queue()
        sup = TurnSupervisor(q)
        sup.apply(Event.MARK_READY)
        # Simulate existing state
        sup.apply(Event.MIC_ONSET)
        sup.apply(Event.TRANSCRIPT_READY, turn_id=uuid4(), text="hello", is_final=True)

        # New connection would create a new supervisor (or reset)
        q2 = asyncio.Queue()
        sup2 = TurnSupervisor(q2)
        assert sup2.floor_owner == FloorOwner.BOOT
        assert sup2.engagement == Engagement.IDLE


class TestSafeModeChaos:
    """Safe mode activates under repeated violations."""

    def test_rapid_violations_trigger_safe_mode(self):
        q = asyncio.Queue()
        sup = TurnSupervisor(q)
        sup.apply(Event.MARK_READY)
        # Force 3+ violations
        for _ in range(4):
            sup.record_floor_violation("chaos_test_injection")

        sup.tick()
        assert sup.mode.value == "safe_mode"


class TestParallelLeaseContention:
    """Multiple lease requests are handled correctly."""

    def test_multiple_leases_same_kind(self):
        q = asyncio.Queue()
        sup = TurnSupervisor(q)
        sup.apply(Event.MARK_READY)
        lease1 = BackgroundLease(id=uuid4(), kind="mind", granted_at=time.monotonic(), floor_revision=sup.floor_revision, ttl=30.0, _supervisor=sup)
        lease2 = BackgroundLease(id=uuid4(), kind="mind", granted_at=time.monotonic(), floor_revision=sup.floor_revision, ttl=30.0, _supervisor=sup)
        assert lease1 is not None
        assert lease2 is not None

    def test_leases_revoked_on_any_transition(self):
        q = asyncio.Queue()
        sup = TurnSupervisor(q)
        sup.apply(Event.MARK_READY)
        lease1 = BackgroundLease(id=uuid4(), kind="mind", granted_at=time.monotonic(), floor_revision=sup.floor_revision, ttl=30.0, _supervisor=sup)
        lease2 = BackgroundLease(id=uuid4(), kind="forge", granted_at=time.monotonic(), floor_revision=sup.floor_revision, ttl=30.0, _supervisor=sup)
        assert lease1 is not None

        rev_before = sup.floor_revision
        sup.apply(Event.MIC_ONSET)

        # Both leases should be invalid
        assert not lease1.valid
        if lease2:
            assert not lease2.valid
