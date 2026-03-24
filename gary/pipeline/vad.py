"""
pipeline/vad.py — Voice Activity Detection with Rolling Buffer & Speech Probability

Three-layer design:
  1. RollingBuffer     — Always keeps the last N seconds of audio in a circular buffer.
                         When the tripwire fires, this pre-roll is prepended so the ASR
                         receives the complete utterance with no leading clipping.

  2. SpeechDetector    — Per-chunk speech probability, 0.0–1.0.
                         Uses spectral band energy ratio (300–3500 Hz vs. total) × RMS.
                         Runtime: ~0.2ms per 500ms chunk on M1 (entirely numpy FFT).

  3. VADAccumulator    — Tripwire state machine.  Fires on_utterance(audio_np) the moment
                         silence is detected after a speech segment.  Flushes the rolling
                         pre-roll + accumulated speech so ASR always gets the full context.

Speech probability is returned by push() so callers can stream it to the browser.

Usage:
    detector = SpeechDetector()
    rolling  = RollingBuffer(capacity_sec=5.0)
    vad      = VADAccumulator(on_utterance=my_cb)

    prob = detector.probability(chunk)          # 0.0 – 1.0, immediate
    rolling.push(chunk)
    vad.push(chunk, preroll_source=rolling)
"""

import time
import numpy as np
import logging
from typing import Callable

log = logging.getLogger("gary.vad")

# ──────────────────────────────────────────────────────────────────────────────
# Tuning constants
# ──────────────────────────────────────────────────────────────────────────────
_SAMPLE_RATE        = 16_000
_SILENCE_HANG_SEC   = 0.55   # seconds of silence after speech to trigger
_MIN_SPEECH_SEC     = 0.15   # minimum accepted utterance duration
_PREROLL_SEC        = 5.0    # rolling buffer duration (seconds)

# SpeechDetector thresholds (both must pass to give high probability)
_RMS_FLOOR          = 0.004  # below this → near-zero probability
_RMS_SATURATE       = 0.06   # at this RMS → full contribution from energy
_SPEECH_HZ_LO       = 300    # lower bound of human speech band
_SPEECH_HZ_HI       = 3500   # upper bound of human speech band
_BAND_RATIO_FLOOR   = 0.30   # if speech-band energy < 30% of total → noise likely
_BAND_RATIO_SAT     = 0.60   # above 60% speech-band ratio → confident speech
# Barge-in tripwire (used by server.py)
BARGEIN_PROB        = 0.70   # probability threshold to trigger barge-in
BARGEIN_FRAMES      = 2      # consecutive frames above threshold → interrupt (~1 second chunks)


# ──────────────────────────────────────────────────────────────────────────────
# 1. RollingBuffer — O(1) amortized push, O(N) snapshot
# ──────────────────────────────────────────────────────────────────────────────
class RollingBuffer:
    """
    Fixed-capacity circular buffer of float32 PCM samples.
    Always holds the LAST `capacity_sec` seconds of audio.
    Very low memory: 5 s × 16000 Hz × 4 bytes = 320 KB.
    """

    def __init__(self, capacity_sec: float = _PREROLL_SEC, sample_rate: int = _SAMPLE_RATE):
        self._capacity = int(capacity_sec * sample_rate)
        self._buf      = np.zeros(self._capacity, dtype=np.float32)
        self._write    = 0          # next write position
        self._filled   = False      # True once we've wrapped at least once

    def push(self, chunk: np.ndarray) -> None:
        """Insert chunk into the ring. Overwrites oldest data when full."""
        n = len(chunk)
        if n == 0:
            return
        if n >= self._capacity:
            # Chunk larger than ring: just keep the tail
            self._buf[:] = chunk[-self._capacity:]
            self._write  = 0
            self._filled = True
            return

        end = self._write + n
        if end <= self._capacity:
            self._buf[self._write:end] = chunk
        else:
            split = self._capacity - self._write
            self._buf[self._write:] = chunk[:split]
            self._buf[:n - split]   = chunk[split:]
            self._filled = True

        self._write = end % self._capacity
        if not self._filled and self._write < n:
            self._filled = True

    def snapshot(self) -> np.ndarray:
        """Return a contiguous copy of all buffered audio in time order."""
        if not self._filled:
            return self._buf[:self._write].copy()
        return np.concatenate([self._buf[self._write:], self._buf[:self._write]])

    def clear(self) -> None:
        self._buf[:] = 0.0
        self._write  = 0
        self._filled = False

    @property
    def duration_sec(self) -> float:
        samples = self._capacity if self._filled else self._write
        return samples / _SAMPLE_RATE


# ──────────────────────────────────────────────────────────────────────────────
# 2. SpeechDetector — spectral + RMS speech probability
# ──────────────────────────────────────────────────────────────────────────────
class SpeechDetector:
    """
    Computes a speech probability (0.0–1.0) for each PCM chunk using:

      1. RMS energy       — scales from 0 at _RMS_FLOOR to 1 at _RMS_SATURATE
      2. Speech band ratio — fraction of FFT energy in 300–3500 Hz
                            scales from 0 at _BAND_RATIO_FLOOR to 1 at _BAND_RATIO_SAT

      final_prob = rms_score * band_score

    No model, no ML, no I/O.  Runtime: ~0.2–0.5 ms per 500ms chunk on M1.
    """

    def __init__(self, sample_rate: int = _SAMPLE_RATE):
        self._sr = sample_rate

    def probability(self, chunk: np.ndarray) -> float:
        """
        Return speech probability in [0, 1] for a PCM chunk.
        Safe to call from any thread.
        """
        if len(chunk) == 0:
            return 0.0

        chunk = chunk.astype(np.float32)

        # ── RMS score ──────────────────────────────────────────────────────────
        rms = float(np.sqrt(np.mean(chunk ** 2)))
        if rms < _RMS_FLOOR:
            return 0.0  # fast exit — definitely silence

        rms_score = min(1.0, (rms - _RMS_FLOOR) / (_RMS_SATURATE - _RMS_FLOOR))

        # ── Spectral band ratio ────────────────────────────────────────────────
        # rfft gives n//2+1 bins; bin k = freq k * (sr / n)
        n    = len(chunk)
        mag  = np.abs(np.fft.rfft(chunk))  # magnitude spectrum
        mag2 = mag ** 2                      # power spectrum

        total_power = float(np.sum(mag2)) + 1e-12

        # Bin indices for speech band
        lo_bin = max(1, int(_SPEECH_HZ_LO * n / self._sr))
        hi_bin = min(len(mag) - 1, int(_SPEECH_HZ_HI * n / self._sr))
        speech_power = float(np.sum(mag2[lo_bin:hi_bin + 1]))

        ratio = speech_power / total_power
        if ratio < _BAND_RATIO_FLOOR:
            band_score = 0.0
        else:
            band_score = min(1.0, (ratio - _BAND_RATIO_FLOOR) / (_BAND_RATIO_SAT - _BAND_RATIO_FLOOR))

        return round(rms_score * band_score, 3)


# ──────────────────────────────────────────────────────────────────────────────
# v3.1 endpointing constants — two-stage with hysteresis
# ──────────────────────────────────────────────────────────────────────────────
ONSET_PROB_THRESHOLD   = 0.50    # enter speech (higher bar = fewer false starts)
KEEP_PROB_THRESHOLD    = 0.30    # stay in speech (hysteresis: easier to stay active)
END_PROB_THRESHOLD     = 0.20    # exit speech (must clearly stop)
SOFT_SILENCE_SEC       = 0.70    # candidate end — trailing silence before first stage
COMMIT_GRACE_SEC       = 0.28    # grace window before final commit
MIN_SPEECH_SEC         = 0.20    # minimum accepted utterance duration (was 0.15)
MAX_UTTERANCE_SEC      = 30.0    # force-finalize if utterance exceeds this
GHOST_DENSITY_THRESHOLD= 0.05    # if speech is <5% of a long buffer, it's ghost noise
# Backward compat aliases
_SILENCE_HANG_SEC      = SOFT_SILENCE_SEC
_MIN_SPEECH_SEC        = MIN_SPEECH_SEC


# ──────────────────────────────────────────────────────────────────────────────
# 3. VADAccumulator — two-stage endpointing FSM with rolling pre-roll (v3.1)
# ──────────────────────────────────────────────────────────────────────────────
class VADAccumulator:
    """
    Stateful VAD with a four-state FSM for robust endpointing:

        idle → in_speech → trailing_silence → commit_pending → [fire]
                               ↑                    |
                               └── [speech resumes] ←┘  (merge-on-resume)

    Key v3.1 improvements over v3.0:
      - Hysteresis: higher threshold to enter speech, lower to stay in it
      - Two-stage commit: trailing silence → grace window → fire
      - Merge-on-resume: speech during grace merges into the same utterance
      - Dynamic hang: short utterances and mid-pause utterances get extra time
      - preempt_for_user(): clears accumulated (echo) audio but keeps rolling pre-buffer

    Parameters
    ----------
    on_utterance     : callback(np.ndarray) — called with complete utterance audio
    rolling          : optional RollingBuffer — used for pre-roll on utterance fire
    silence_hang_sec : (deprecated) — use SOFT_SILENCE_SEC constant instead
    """

    # FSM states
    _S_IDLE             = "idle"
    _S_IN_SPEECH        = "in_speech"
    _S_TRAILING_SILENCE = "trailing_silence"
    _S_COMMIT_PENDING   = "commit_pending"

    def __init__(
        self,
        on_utterance: Callable[[np.ndarray], None],
        rolling: "RollingBuffer | None" = None,
        silence_hang_sec: float = SOFT_SILENCE_SEC,
    ):
        self._on_utterance   = on_utterance
        self._rolling        = rolling
        self._hang_sec       = silence_hang_sec
        self._buffer: list   = []
        self._total_samples  = 0
        self._speech_samples = 0
        self._last_speech_ts = None
        self._state          = self._S_IDLE
        self._trailing_start = None  # when trailing_silence began
        self._commit_start   = None  # when commit_pending began
        self._had_mid_pause  = False  # did the user resume after trailing_silence?

    @staticmethod
    def _rms(chunk: np.ndarray) -> float:
        return float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2)))

    def _effective_hang_sec(self) -> float:
        """Dynamic silence hang — short or paused utterances get extra patience."""
        hang = self._hang_sec
        speech_dur = self._total_samples / _SAMPLE_RATE
        if speech_dur < 1.5:
            hang += 0.15   # short utterances: user may still be thinking
        if self._had_mid_pause:
            hang += 0.10   # user already paused mid-thought
        return min(hang, 1.10)  # hard cap to avoid excessive delay

    @property
    def state(self) -> str:
        """Current FSM state (for testing and logging)."""
        return self._state

    def push(self, chunk: np.ndarray, prob: float = -1.0) -> bool:
        """
        Push a PCM chunk.
        prob: speech probability from SpeechDetector (-1 = recompute from RMS only)
        Returns True if an utterance was fired.
        """
        # Determine speech/silence with hysteresis
        if prob >= 0.0:
            if self._state == self._S_IDLE:
                is_speech = prob > ONSET_PROB_THRESHOLD
            elif self._state == self._S_IN_SPEECH:
                is_speech = prob > KEEP_PROB_THRESHOLD
            elif self._state in (self._S_TRAILING_SILENCE, self._S_COMMIT_PENDING):
                # Need clear onset to re-enter speech (merge)
                is_speech = prob > ONSET_PROB_THRESHOLD
            else:
                is_speech = prob > ONSET_PROB_THRESHOLD
        else:
            is_speech = self._rms(chunk) > 0.005

        self._buffer.append(chunk)
        self._total_samples += len(chunk)
        if is_speech:
            self._speech_samples += len(chunk)

        now = time.monotonic()

        # ── Ghost Accumulation & Clamp ────────────────────────────────────────
        current_dur = self._total_samples / _SAMPLE_RATE
        if current_dur > MAX_UTTERANCE_SEC:
            log.warning("MAX_UTTERANCE_SEC (%.1fs) exceeded. Force-finalizing.", MAX_UTTERANCE_SEC)
            self._flush()
            return True

        # If we have a long buffer (e.g. >10s) but very little speech, it's ghost noise
        if current_dur > 10.0 and (self._speech_samples / self._total_samples) < GHOST_DENSITY_THRESHOLD:
            log.warning("Ghost accumulation detected: density <%.0f%% over %.1fs. Force-resetting.",
                        GHOST_DENSITY_THRESHOLD * 100, current_dur)
            self._reset()
            return False

        # ── State machine transitions ──────────────────────────────────────────
        if self._state == self._S_IDLE:
            if is_speech:
                self._state = self._S_IN_SPEECH
                self._last_speech_ts = now
                log.debug("VAD: idle → in_speech")

        elif self._state == self._S_IN_SPEECH:
            if is_speech:
                self._last_speech_ts = now
            else:
                # Speech stopped — check if enough silence for candidate end
                silence_dur = now - self._last_speech_ts if self._last_speech_ts else 0
                if silence_dur >= self._effective_hang_sec():
                    self._state = self._S_TRAILING_SILENCE
                    self._trailing_start = now
                    log.debug("VAD: in_speech → trailing_silence (%.0fms silence)",
                              silence_dur * 1000)

        elif self._state == self._S_TRAILING_SILENCE:
            if is_speech:
                # Merge-on-resume: go back to in_speech, keep all audio
                self._state = self._S_IN_SPEECH
                self._last_speech_ts = now
                self._had_mid_pause = True
                log.debug("VAD: trailing_silence → in_speech (merge-on-resume)")
            else:
                # Still silent — advance to commit_pending after grace period
                trailing_dur = now - self._trailing_start if self._trailing_start else 0
                if trailing_dur >= COMMIT_GRACE_SEC:
                    self._state = self._S_COMMIT_PENDING
                    self._commit_start = now
                    log.debug("VAD: trailing_silence → commit_pending")

        elif self._state == self._S_COMMIT_PENDING:
            if is_speech:
                # Last-chance merge: go back to in_speech
                self._state = self._S_IN_SPEECH
                self._last_speech_ts = now
                self._had_mid_pause = True
                log.debug("VAD: commit_pending → in_speech (last-chance merge)")
            else:
                # Final commit
                speech_dur = self._total_samples / _SAMPLE_RATE
                if speech_dur >= MIN_SPEECH_SEC:
                    self._flush()
                    return True
                else:
                    self._reset()

        return False

    def finalize(self) -> None:
        """Force-flush any buffered audio (connection close, explicit stop)."""
        if self._state != self._S_IDLE and self._total_samples / _SAMPLE_RATE >= MIN_SPEECH_SEC:
            self._flush()

    def preempt_for_user(self) -> None:
        """Cancel agent state but keep rolling pre-buffer alive.

        Clears the _accumulated_ buffer (which contains echo audio during
        agent speech) but does NOT clear the RollingBuffer.  The next
        utterance fire will prepend the rolling pre-roll snapshot,
        preserving the user's onset syllables.
        """
        self._buffer         = []
        self._total_samples  = 0
        self._speech_samples = 0
        self._last_speech_ts = None
        self._state          = self._S_IDLE
        self._trailing_start = None
        self._commit_start   = None
        self._had_mid_pause  = False
        # NOTE: self._rolling is intentionally NOT cleared

    def _flush(self) -> None:
        current = np.concatenate(self._buffer) if self._buffer else np.zeros(0, dtype=np.float32)

        # Prepend pre-roll from rolling buffer (deduped: rolling may already contain
        # some of the accumulated frames, so we trim by total accumulated length)
        if self._rolling is not None:
            preroll = self._rolling.snapshot()
            # Trim the tail of preroll that overlaps with current (avoid double-counting)
            overlap = min(len(preroll), self._total_samples)
            preroll = preroll[:max(0, len(preroll) - overlap)]
            if len(preroll) > 0:
                audio = np.concatenate([preroll, current])
            else:
                audio = current
        else:
            audio = current

        dur = len(audio) / _SAMPLE_RATE
        log.debug(f"VAD: firing utterance {dur:.2f}s (accumulated {self._total_samples/_SAMPLE_RATE:.2f}s + preroll)")
        self._on_utterance(audio)
        self._reset()

    def _reset(self) -> None:
        self._buffer         = []
        self._total_samples  = 0
        self._speech_samples = 0
        self._last_speech_ts = None
        self._state          = self._S_IDLE
        self._trailing_start = None
        self._commit_start   = None
        self._had_mid_pause  = False

