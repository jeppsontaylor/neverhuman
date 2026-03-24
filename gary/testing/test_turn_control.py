"""
testing/test_turn_control.py — Tests for v3.1 turn-control components

Validates:
  - TurnSupervisor: epoch tracking, floor state, preemption, task cancellation
  - VADAccumulator FSM: state transitions, hysteresis, merge-on-resume, dynamic hang
  - preempt_for_user: clears accumulated buffer but preserves rolling pre-buffer
  - Constants and thresholds
"""
import asyncio
import time
import numpy as np
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline.vad import (
    VADAccumulator, RollingBuffer, SpeechDetector,
    ONSET_PROB_THRESHOLD, KEEP_PROB_THRESHOLD, END_PROB_THRESHOLD,
    SOFT_SILENCE_SEC, COMMIT_GRACE_SEC, MIN_SPEECH_SEC,
    _SAMPLE_RATE,
)
from pipeline.turn_supervisor import TurnSupervisor, FloorState, FloorOwner, Engagement, Event
from pipeline._ws_helpers import safe_send_json, safe_send_bytes


# ── TurnSupervisor ────────────────────────────────────────────────────────────

class TestTurnSupervisor:
    """Tests for the centralized turn/epoch manager."""

    def test_initial_epoch_is_zero(self):
        sup = TurnSupervisor(asyncio.Queue())
        assert sup.turn_epoch == 0

    def test_initial_floor_is_booting(self):
        sup = TurnSupervisor(asyncio.Queue())
        assert sup.floor == FloorState.BOOTING

    def test_start_foreground_sets_floor_thinking(self):
        sup = TurnSupervisor(asyncio.Queue())

        async def dummy(): await asyncio.sleep(10)
        loop = asyncio.new_event_loop()
        task = loop.create_task(dummy())
        sup.start_foreground(task)
        assert sup.floor == FloorState.FOREGROUND_THINKING
        task.cancel()
        loop.close()

    def test_set_speaking_changes_floor(self):
        sup = TurnSupervisor(asyncio.Queue())
        sup.set_speaking()
        assert sup.floor == FloorState.AGENT_SPEAKING

    def test_end_turn_resets_to_cooldown(self):
        sup = TurnSupervisor(asyncio.Queue())
        sup.set_speaking()
        sup.on_tts_finished()
        assert sup.floor == FloorState.COOLDOWN
        assert sup.active_turn_task is None

    @pytest.mark.asyncio
    async def test_preempt_increments_epoch(self):
        sup = TurnSupervisor(asyncio.Queue())
        old_epoch = sup.turn_epoch
        await sup.preempt(ws=None)
        assert sup.turn_epoch == old_epoch + 1

    @pytest.mark.asyncio
    async def test_preempt_sets_floor_to_user_acquiring(self):
        sup = TurnSupervisor(asyncio.Queue())
        sup.set_speaking()
        await sup.preempt(ws=None)
        assert sup.floor == FloorState.USER_ACQUIRING

    @pytest.mark.asyncio
    async def test_preempt_cancels_active_turn_task(self):
        """Preempt should cancel the registered turn task."""
        sup = TurnSupervisor(asyncio.Queue())

        async def slow_work():
            await asyncio.sleep(10)

        task = asyncio.create_task(slow_work())
        sup.active_turn_task = task
        await sup.preempt(ws=None)
        # After preempt + shield wait, task should be cancelled
        await asyncio.sleep(0.05)
        assert task.cancelled()

    @pytest.mark.asyncio
    async def test_preempt_cancels_filler_task(self):
        """Preempt should cancel the filler audio task."""
        sup = TurnSupervisor(asyncio.Queue())

        async def slow_filler():
            await asyncio.sleep(10)

        task = asyncio.create_task(slow_filler())
        sup.filler_task = task
        await sup.preempt(ws=None)
        await asyncio.sleep(0.05)
        assert task.cancelled()

    @pytest.mark.asyncio
    async def test_preempt_drains_utterance_queue(self):
        """Preempt should remove all pending utterances from the queue."""
        q = asyncio.Queue()
        sup = TurnSupervisor(q)
        q.put_nowait(np.zeros(1000, dtype=np.float32))
        q.put_nowait(np.zeros(2000, dtype=np.float32))
        await sup.preempt(ws=None)
        assert q.empty()

    @pytest.mark.asyncio
    async def test_preempt_multiple_times(self):
        """Multiple preempts should keep incrementing epoch."""
        sup = TurnSupervisor(asyncio.Queue())
        for i in range(5):
            await sup.preempt(ws=None)
        assert sup.turn_epoch == 5

    def test_is_stale_current_epoch(self):
        """is_stale returns False for the current epoch."""
        sup = TurnSupervisor(asyncio.Queue())
        assert sup.is_stale(0) is False

    @pytest.mark.asyncio
    async def test_is_stale_after_preempt(self):
        """is_stale returns True for old epoch after preempt."""
        sup = TurnSupervisor(asyncio.Queue())
        await sup.preempt(ws=None)
        assert sup.is_stale(0) is True
        assert sup.is_stale(1) is False


# ── VAD FSM States ────────────────────────────────────────────────────────────

class TestVADFSMStates:
    """Tests for the fine-grained FSM state transitions."""

    _SPEECH_LEN = 25000  # 1.5625s — avoids dynamic hang extension

    def test_initial_state_is_idle(self):
        vad = VADAccumulator(on_utterance=lambda a: None)
        assert vad.state == "idle"

    def test_speech_transitions_to_in_speech(self):
        vad = VADAccumulator(on_utterance=lambda a: None)
        vad.push(np.ones(4000, dtype=np.float32) * 0.1, prob=0.9)
        assert vad.state == "in_speech"

    def test_silence_below_onset_stays_idle(self):
        """Silence with prob below ONSET_PROB_THRESHOLD stays idle."""
        vad = VADAccumulator(on_utterance=lambda a: None)
        vad.push(np.zeros(4000, dtype=np.float32), prob=0.3)
        assert vad.state == "idle"

    def test_in_speech_stays_with_keep_threshold(self):
        """Once in_speech, a lower prob (above KEEP) still counts as speech."""
        vad = VADAccumulator(on_utterance=lambda a: None)
        vad.push(np.ones(4000, dtype=np.float32) * 0.1, prob=0.9)  # enter
        vad.push(np.ones(4000, dtype=np.float32) * 0.1, prob=0.35)  # stay (> KEEP 0.30)
        assert vad.state == "in_speech"

    def test_trailing_silence_after_hang(self):
        """After sufficient silence, transitions to trailing_silence."""
        vad = VADAccumulator(on_utterance=lambda a: None, silence_hang_sec=0.01)
        vad.push(np.ones(self._SPEECH_LEN, dtype=np.float32) * 0.1, prob=0.9)
        time.sleep(0.02)
        vad.push(np.zeros(4000, dtype=np.float32), prob=0.0)
        assert vad.state == "trailing_silence"

    def test_merge_on_resume_from_trailing(self):
        """Speech during trailing_silence merges back to in_speech."""
        vad = VADAccumulator(on_utterance=lambda a: None, silence_hang_sec=0.01)
        vad.push(np.ones(self._SPEECH_LEN, dtype=np.float32) * 0.1, prob=0.9)
        time.sleep(0.02)
        vad.push(np.zeros(4000, dtype=np.float32), prob=0.0)  # → trailing_silence
        assert vad.state == "trailing_silence"

        # Speech resumes → merge back
        vad.push(np.ones(4000, dtype=np.float32) * 0.1, prob=0.9)
        assert vad.state == "in_speech"

    def test_commit_pending_reached(self):
        """After trailing_silence + COMMIT_GRACE, reaches commit_pending."""
        vad = VADAccumulator(on_utterance=lambda a: None, silence_hang_sec=0.01)
        vad.push(np.ones(self._SPEECH_LEN, dtype=np.float32) * 0.1, prob=0.9)
        time.sleep(0.02)
        vad.push(np.zeros(4000, dtype=np.float32), prob=0.0)  # → trailing_silence
        time.sleep(COMMIT_GRACE_SEC + 0.02)
        vad.push(np.zeros(4000, dtype=np.float32), prob=0.0)  # → commit_pending
        assert vad.state == "commit_pending"

    def test_last_chance_merge_from_commit(self):
        """Speech during commit_pending merges back (last-chance merge)."""
        vad = VADAccumulator(on_utterance=lambda a: None, silence_hang_sec=0.01)
        vad.push(np.ones(self._SPEECH_LEN, dtype=np.float32) * 0.1, prob=0.9)
        time.sleep(0.02)
        vad.push(np.zeros(4000, dtype=np.float32), prob=0.0)
        time.sleep(COMMIT_GRACE_SEC + 0.02)
        vad.push(np.zeros(4000, dtype=np.float32), prob=0.0)  # → commit_pending
        assert vad.state == "commit_pending"

        # Speech resumes → last-chance merge
        vad.push(np.ones(4000, dtype=np.float32) * 0.1, prob=0.9)
        assert vad.state == "in_speech"


# ── preempt_for_user ──────────────────────────────────────────────────────────

class TestPreemptForUser:
    """Tests for VADAccumulator.preempt_for_user()."""

    def test_preserves_rolling_buffer(self):
        """preempt_for_user must NOT clear the rolling pre-buffer."""
        rolling = RollingBuffer(capacity_sec=1.0, sample_rate=16000)
        marker = np.ones(8000, dtype=np.float32) * 0.42
        rolling.push(marker)

        vad = VADAccumulator(on_utterance=lambda a: None, rolling=rolling)
        vad.push(np.ones(4000, dtype=np.float32) * 0.1, prob=0.9)

        vad.preempt_for_user()

        # Rolling buffer should still have the marker data
        snap = rolling.snapshot()
        assert len(snap) == 8000
        assert np.allclose(snap, 0.42)

    def test_clears_accumulated_buffer(self):
        """preempt_for_user must clear the accumulated (echo) buffer."""
        vad = VADAccumulator(on_utterance=lambda a: None)
        vad.push(np.ones(8000, dtype=np.float32) * 0.1, prob=0.9)
        assert vad._total_samples == 8000

        vad.preempt_for_user()

        assert vad._total_samples == 0
        assert len(vad._buffer) == 0

    def test_resets_state_to_idle(self):
        """preempt_for_user should set state back to idle."""
        vad = VADAccumulator(on_utterance=lambda a: None)
        vad.push(np.ones(4000, dtype=np.float32) * 0.1, prob=0.9)
        assert vad.state == "in_speech"

        vad.preempt_for_user()
        assert vad.state == "idle"

    def test_clears_mid_pause_flag(self):
        """preempt_for_user should clear _had_mid_pause."""
        vad = VADAccumulator(on_utterance=lambda a: None, silence_hang_sec=0.01)
        vad.push(np.ones(25000, dtype=np.float32) * 0.1, prob=0.9)
        time.sleep(0.02)
        vad.push(np.zeros(4000, dtype=np.float32), prob=0.0)
        vad.push(np.ones(4000, dtype=np.float32) * 0.1, prob=0.9)  # merge
        assert vad._had_mid_pause is True

        vad.preempt_for_user()
        assert vad._had_mid_pause is False


# ── Hysteresis Thresholds ─────────────────────────────────────────────────────

class TestHysteresisThresholds:
    """Validate the asymmetric threshold design."""

    def test_onset_higher_than_keep(self):
        """It should be harder to START speech than to CONTINUE it."""
        assert ONSET_PROB_THRESHOLD > KEEP_PROB_THRESHOLD

    def test_keep_higher_than_end(self):
        """It should be easier to stay in speech than to leave it."""
        assert KEEP_PROB_THRESHOLD > END_PROB_THRESHOLD

    def test_onset_below_bargein(self):
        """Barge-in threshold should be higher than onset (to avoid accidental interrupts)."""
        from pipeline.vad import BARGEIN_PROB
        assert BARGEIN_PROB > ONSET_PROB_THRESHOLD

    def test_soft_silence_reasonable(self):
        """Soft silence hang should be between 0.3 and 2.0 seconds."""
        assert 0.3 <= SOFT_SILENCE_SEC <= 2.0

    def test_commit_grace_reasonable(self):
        """Commit grace should be between 0.1 and 0.5 seconds."""
        assert 0.1 <= COMMIT_GRACE_SEC <= 0.5

    def test_min_speech_reasonable(self):
        """Minimum speech duration should be between 0.1 and 0.5 seconds."""
        assert 0.1 <= MIN_SPEECH_SEC <= 0.5


# ── Dynamic Hang Extension ───────────────────────────────────────────────────

class TestDynamicHangExtension:
    """Validate the dynamic silence hang extension behavior."""

    def test_short_utterance_gets_extra_time(self):
        """Utterances < 1.5s should get an extra 0.15s hang."""
        vad = VADAccumulator(on_utterance=lambda a: None, silence_hang_sec=0.70)
        vad._total_samples = int(1.0 * _SAMPLE_RATE)  # 1.0s
        hang = vad._effective_hang_sec()
        assert hang > 0.70  # should have the +0.15 extension

    def test_long_utterance_no_extra_time(self):
        """Utterances >= 1.5s should NOT get the short-utterance extension."""
        vad = VADAccumulator(on_utterance=lambda a: None, silence_hang_sec=0.70)
        vad._total_samples = int(2.0 * _SAMPLE_RATE)  # 2.0s
        hang = vad._effective_hang_sec()
        assert hang == 0.70

    def test_mid_pause_adds_extra_time(self):
        """Utterances with mid-pause should get an extra 0.10s hang."""
        vad = VADAccumulator(on_utterance=lambda a: None, silence_hang_sec=0.70)
        vad._total_samples = int(2.0 * _SAMPLE_RATE)  # 2.0s (no short extension)
        vad._had_mid_pause = True
        hang = vad._effective_hang_sec()
        assert hang == pytest.approx(0.80)  # +0.10 for mid-pause

    def test_both_extensions_stack(self):
        """Short utterance + mid-pause should stack both extensions."""
        vad = VADAccumulator(on_utterance=lambda a: None, silence_hang_sec=0.70)
        vad._total_samples = int(1.0 * _SAMPLE_RATE)  # 1.0s
        vad._had_mid_pause = True
        hang = vad._effective_hang_sec()
        assert hang == 0.70 + 0.15 + 0.10  # 0.95

    def test_hard_cap_prevents_excessive_delay(self):
        """Dynamic hang should never exceed 1.10s."""
        vad = VADAccumulator(on_utterance=lambda a: None, silence_hang_sec=1.05)
        vad._total_samples = int(0.5 * _SAMPLE_RATE)  # short
        vad._had_mid_pause = True
        hang = vad._effective_hang_sec()
        assert hang <= 1.10


# ── _ws_helpers ───────────────────────────────────────────────────────────────

class TestWSHelpers:
    """Test the extracted WebSocket helper functions."""

    def test_safe_send_json_exists(self):
        """safe_send_json should be importable."""
        assert callable(safe_send_json)

    def test_safe_send_bytes_exists(self):
        """safe_send_bytes should be importable."""
        assert callable(safe_send_bytes)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
