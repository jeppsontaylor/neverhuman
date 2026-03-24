"""
testing/test_vad.py — Tests for the Voice Activity Detection pipeline

Validates:
  - RollingBuffer: circular buffer correctness, wrap-around, snapshot
  - SpeechDetector: probability scoring for silence, noise, speech-band signals
  - VADAccumulator: tripwire state machine, silence hangtime, pre-roll, finalize
"""
import time
import numpy as np
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline.vad import (
    RollingBuffer, SpeechDetector, VADAccumulator,
    _SAMPLE_RATE, _SILENCE_HANG_SEC, _MIN_SPEECH_SEC,
    BARGEIN_PROB, BARGEIN_FRAMES,
)


# ── RollingBuffer ─────────────────────────────────────────────────────────────

class TestRollingBuffer:
    def test_empty_snapshot(self):
        rb = RollingBuffer(capacity_sec=1.0)
        snap = rb.snapshot()
        assert len(snap) == 0

    def test_push_and_snapshot(self):
        rb = RollingBuffer(capacity_sec=1.0, sample_rate=16000)
        chunk = np.ones(4000, dtype=np.float32) * 0.5
        rb.push(chunk)
        snap = rb.snapshot()
        assert len(snap) == 4000
        assert np.allclose(snap, 0.5)

    def test_wrap_around(self):
        rb = RollingBuffer(capacity_sec=0.5, sample_rate=16000)
        capacity = int(0.5 * 16000)  # 8000 samples

        # Push 6000, then 6000 again — should wrap
        chunk1 = np.ones(6000, dtype=np.float32) * 1.0
        chunk2 = np.ones(6000, dtype=np.float32) * 2.0
        rb.push(chunk1)
        rb.push(chunk2)

        snap = rb.snapshot()
        assert len(snap) == capacity
        # Snapshot should contain the most recent 8000 samples
        # Last 6000 from chunk2, first 2000 from chunk1
        assert snap[-1] == 2.0

    def test_oversized_chunk(self):
        """A chunk larger than capacity keeps only the tail."""
        rb = RollingBuffer(capacity_sec=0.5, sample_rate=16000)
        capacity = int(0.5 * 16000)
        big = np.arange(capacity * 3, dtype=np.float32)
        rb.push(big)
        snap = rb.snapshot()
        assert len(snap) == capacity
        # Should be the last `capacity` samples of big
        np.testing.assert_array_equal(snap, big[-capacity:])

    def test_clear(self):
        rb = RollingBuffer(capacity_sec=1.0)
        rb.push(np.ones(1000, dtype=np.float32))
        rb.clear()
        snap = rb.snapshot()
        assert len(snap) == 0

    def test_duration_sec(self):
        rb = RollingBuffer(capacity_sec=2.0, sample_rate=16000)
        assert rb.duration_sec == 0.0
        rb.push(np.ones(16000, dtype=np.float32))
        assert abs(rb.duration_sec - 1.0) < 0.01

    def test_duration_sec_after_fill(self):
        rb = RollingBuffer(capacity_sec=1.0, sample_rate=16000)
        rb.push(np.ones(32000, dtype=np.float32))  # 2 seconds into 1s buffer
        assert abs(rb.duration_sec - 1.0) < 0.01


# ── SpeechDetector ────────────────────────────────────────────────────────────

class TestSpeechDetector:
    def setup_method(self):
        self.det = SpeechDetector(sample_rate=16000)

    def test_empty_chunk_returns_zero(self):
        assert self.det.probability(np.array([], dtype=np.float32)) == 0.0

    def test_silence_returns_zero(self):
        silence = np.zeros(8000, dtype=np.float32)
        assert self.det.probability(silence) == 0.0

    def test_very_quiet_returns_zero(self):
        quiet = np.random.randn(8000).astype(np.float32) * 0.001
        prob = self.det.probability(quiet)
        assert prob < 0.1

    def test_loud_wideband_noise(self):
        """Loud white noise has energy in all bands, not just speech."""
        noise = np.random.randn(8000).astype(np.float32) * 0.1
        prob = self.det.probability(noise)
        # White noise should have moderate band ratio — not clearly speech
        assert isinstance(prob, float)
        assert 0.0 <= prob <= 1.0

    def test_speech_band_sine(self):
        """A pure tone in the speech band (1000 Hz) should score higher."""
        t = np.arange(8000, dtype=np.float32) / 16000.0
        speech_tone = np.sin(2 * np.pi * 1000 * t).astype(np.float32) * 0.05
        prob_speech = self.det.probability(speech_tone)

        # A tone outside speech band (100 Hz)
        low_tone = np.sin(2 * np.pi * 100 * t).astype(np.float32) * 0.05
        prob_low = self.det.probability(low_tone)

        # Speech-band tone should score higher
        assert prob_speech > prob_low

    def test_returns_between_zero_and_one(self):
        chunk = np.random.randn(8000).astype(np.float32) * 0.03
        prob = self.det.probability(chunk)
        assert 0.0 <= prob <= 1.0

    def test_probability_is_rounded(self):
        t = np.arange(8000, dtype=np.float32) / 16000.0
        tone = np.sin(2 * np.pi * 1000 * t).astype(np.float32) * 0.05
        prob = self.det.probability(tone)
        # Should be rounded to 3 decimal places
        assert prob == round(prob, 3)


# ── VADAccumulator ────────────────────────────────────────────────────────────

class TestVADAccumulator:
    """Tests for the two-stage VAD endpointing FSM (v3.1).

    Key timing: with silence_hang_sec=0.01, speech >= 1.5s, the FSM
    transitions as follows:
        push(speech, 0.9) → idle → in_speech
        sleep(0.02) + push(silence) → in_speech → trailing_silence
        sleep(COMMIT_GRACE + 0.02) + push(silence) → → commit_pending
        push(silence) → fire
    """

    # Speech that exceeds 1.5s to avoid dynamic hang extension (+0.15s)
    _SPEECH_LEN = 25000  # 1.5625s @ 16kHz

    def test_no_fire_on_silence(self):
        """Silence should never fire the utterance callback."""
        fired = []
        vad = VADAccumulator(on_utterance=lambda a: fired.append(a))
        silence = np.zeros(8000, dtype=np.float32)
        for _ in range(10):
            vad.push(silence, prob=0.0)
        assert len(fired) == 0

    def test_fires_on_speech_then_silence(self):
        """Speech followed by sufficient silence should fire through
        the full in_speech → trailing_silence → commit_pending → fire path."""
        from pipeline.vad import COMMIT_GRACE_SEC

        fired = []
        vad = VADAccumulator(
            on_utterance=lambda a: fired.append(a),
            silence_hang_sec=0.01,
        )

        # Push long speech (avoids dynamic hang extension)
        vad.push(np.ones(self._SPEECH_LEN, dtype=np.float32) * 0.1, prob=0.9)

        # Wait past silence_hang_sec → trailing_silence
        time.sleep(0.02)
        vad.push(np.zeros(4000, dtype=np.float32), prob=0.0)
        assert vad.state == "trailing_silence"

        # Wait past COMMIT_GRACE → commit_pending
        time.sleep(COMMIT_GRACE_SEC + 0.02)
        vad.push(np.zeros(4000, dtype=np.float32), prob=0.0)
        assert vad.state == "commit_pending"

        # One more push → fire
        result = vad.push(np.zeros(4000, dtype=np.float32), prob=0.0)

        assert result is True
        assert len(fired) == 1
        assert len(fired[0]) > 0

    def test_short_speech_rejected(self):
        """Speech shorter than MIN_SPEECH_SEC is discarded at commit."""
        from pipeline.vad import COMMIT_GRACE_SEC

        fired = []
        vad = VADAccumulator(
            on_utterance=lambda a: fired.append(a),
            silence_hang_sec=0.01,
        )

        # Push very tiny speech (100 samples = 6.25ms, well under 200ms minimum)
        vad.push(np.ones(100, dtype=np.float32) * 0.1, prob=0.9)

        # Drive through all stages (dynamic hang applies here but we sleep generously)
        time.sleep(0.40)
        vad.push(np.zeros(100, dtype=np.float32), prob=0.0)
        time.sleep(COMMIT_GRACE_SEC + 0.02)
        vad.push(np.zeros(100, dtype=np.float32), prob=0.0)
        vad.push(np.zeros(100, dtype=np.float32), prob=0.0)

        # Should NOT fire — too short
        assert len(fired) == 0

    def test_finalize_flushes(self):
        """finalize() should flush buffered speech."""
        fired = []
        vad = VADAccumulator(on_utterance=lambda a: fired.append(a))

        speech = np.ones(4000, dtype=np.float32) * 0.1
        vad.push(speech, prob=0.9)
        assert len(fired) == 0

        vad.finalize()
        assert len(fired) == 1

    def test_finalize_ignores_short(self):
        """finalize() should not flush speech that's too short."""
        fired = []
        vad = VADAccumulator(on_utterance=lambda a: fired.append(a))

        tiny = np.ones(100, dtype=np.float32) * 0.1
        vad.push(tiny, prob=0.9)
        vad.finalize()
        assert len(fired) == 0

    def test_preroll_prepended(self):
        """When a RollingBuffer is provided, preroll is prepended."""
        from pipeline.vad import COMMIT_GRACE_SEC

        fired = []
        rolling = RollingBuffer(capacity_sec=1.0, sample_rate=16000)

        # Fill rolling buffer with marker audio
        marker = np.ones(8000, dtype=np.float32) * 0.42
        rolling.push(marker)

        vad = VADAccumulator(
            on_utterance=lambda a: fired.append(a),
            rolling=rolling,
            silence_hang_sec=0.01,
        )

        # Push long speech (avoids dynamic hang extension)
        vad.push(np.ones(self._SPEECH_LEN, dtype=np.float32) * 0.1, prob=0.9)

        # Drive through all stages
        time.sleep(0.02)
        vad.push(np.zeros(4000, dtype=np.float32), prob=0.0)  # → trailing_silence
        time.sleep(COMMIT_GRACE_SEC + 0.02)
        vad.push(np.zeros(4000, dtype=np.float32), prob=0.0)  # → commit_pending
        vad.push(np.zeros(4000, dtype=np.float32), prob=0.0)  # → fire

        assert len(fired) == 1
        # The fired audio should be longer than just the speech
        assert len(fired[0]) >= self._SPEECH_LEN

    def test_reset_after_fire(self):
        """After firing, the VAD should reset and fire again on new speech."""
        from pipeline.vad import COMMIT_GRACE_SEC

        fire_count = [0]
        vad = VADAccumulator(
            on_utterance=lambda a: fire_count.__setitem__(0, fire_count[0] + 1),
            silence_hang_sec=0.01,
        )

        for _ in range(3):
            vad.push(np.ones(self._SPEECH_LEN, dtype=np.float32) * 0.1, prob=0.9)
            time.sleep(0.02)
            vad.push(np.zeros(4000, dtype=np.float32), prob=0.0)  # → trailing_silence
            time.sleep(COMMIT_GRACE_SEC + 0.02)
            vad.push(np.zeros(4000, dtype=np.float32), prob=0.0)  # → commit_pending
            vad.push(np.zeros(4000, dtype=np.float32), prob=0.0)  # → fire

        assert fire_count[0] == 3

    def test_rms_fallback(self):
        """When prob=-1, VADAccumulator falls back to RMS detection."""
        from pipeline.vad import COMMIT_GRACE_SEC

        fired = []
        vad = VADAccumulator(
            on_utterance=lambda a: fired.append(a),
            silence_hang_sec=0.01,
        )

        # Loud enough to pass RMS threshold (>0.005)
        vad.push(np.ones(self._SPEECH_LEN, dtype=np.float32) * 0.05, prob=-1.0)

        time.sleep(0.02)
        vad.push(np.zeros(4000, dtype=np.float32), prob=-1.0)  # → trailing_silence
        time.sleep(COMMIT_GRACE_SEC + 0.02)
        vad.push(np.zeros(4000, dtype=np.float32), prob=-1.0)  # → commit_pending
        vad.push(np.zeros(4000, dtype=np.float32), prob=-1.0)  # → fire

        assert len(fired) == 1


# ── Constants / Config ────────────────────────────────────────────────────────

class TestVADConstants:
    def test_sample_rate(self):
        assert _SAMPLE_RATE == 16_000

    def test_silence_hang_positive(self):
        assert _SILENCE_HANG_SEC > 0

    def test_min_speech_positive(self):
        assert _MIN_SPEECH_SEC > 0

    def test_bargein_prob_in_range(self):
        assert 0.0 < BARGEIN_PROB < 1.0

    def test_bargein_frames_positive(self):
        assert BARGEIN_FRAMES >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
