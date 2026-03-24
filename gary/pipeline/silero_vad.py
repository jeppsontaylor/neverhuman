"""
pipeline/silero_vad.py — Silero VAD v5 wrapper for GARY v2

Replaces the spectral-based SpeechDetector with Silero's pre-trained
ONNX model for significantly more accurate speech detection.

Performance: <2ms per 160ms chunk on Apple Silicon (ONNX Runtime).
Model size: ~2MB (downloaded once).

The Silero model expects:
  - 16kHz mono Float32 audio
  - Chunks of 512 samples (32ms) — we batch process our 160ms chunks

Usage:
    vad = SileroVAD()
    prob = vad.probability(chunk_160ms)   # returns 0.0-1.0
"""
from __future__ import annotations

import logging
import os
import urllib.request
from pathlib import Path
from typing import Optional

import numpy as np

log = logging.getLogger("gary.silero_vad")

_SAMPLE_RATE = 16_000
_SILERO_CHUNK = 512   # Silero's native window: 512 samples = 32ms @ 16kHz
_CACHE_DIR = Path.home() / ".cache" / "silero-vad"
_MODEL_URL = "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx"
_MODEL_PATH = _CACHE_DIR / "silero_vad.onnx"


def _ensure_model() -> Path:
    """Download the Silero VAD ONNX model if not cached."""
    if _MODEL_PATH.exists():
        return _MODEL_PATH
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    log.info(f"Downloading Silero VAD model to {_MODEL_PATH}...")
    tmp = _MODEL_PATH.with_suffix(".tmp")
    urllib.request.urlretrieve(_MODEL_URL, tmp)
    tmp.rename(_MODEL_PATH)
    log.info(f"Silero VAD model ready ({_MODEL_PATH.stat().st_size // 1024}KB)")
    return _MODEL_PATH


class SileroVAD:
    """
    Stateful Silero VAD wrapper.

    The ONNX model expects:
      Inputs:  input=[1, chunk_size], state=[2, 1, 128], sr=scalar_int64
      Outputs: output=[1, 1], stateN=[2, 1, 128]

    Call reset() between sessions or when context changes (e.g. barge-in).
    """

    def __init__(self, threshold: float = 0.5, sample_rate: int = _SAMPLE_RATE):
        self._sr = sample_rate
        self._threshold = threshold
        self._session: Optional[object] = None
        self._state = None   # [2, 1, 128] LSTM state

    def _ensure_session(self):
        """Lazy-load the ONNX session."""
        if self._session is not None:
            return

        try:
            import onnxruntime as ort
        except ImportError:
            log.warning("onnxruntime not installed — Silero VAD unavailable. pip install onnxruntime")
            raise

        model_path = _ensure_model()
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.log_severity_level = 3  # suppress verbose ort logs
        self._session = ort.InferenceSession(
            str(model_path), sess_options=opts,
            providers=["CPUExecutionProvider"],
        )
        self.reset()
        log.info("Silero VAD session ready ✓")

    def reset(self):
        """Reset LSTM hidden states. Call between sessions."""
        # Silero VAD v5 uses state of shape [2, 1, 128]
        self._state = np.zeros((2, 1, 128), dtype=np.float32)

    def probability(self, chunk: np.ndarray) -> float:
        """
        Return speech probability [0.0-1.0] for a PCM chunk.

        The chunk can be any length. We process it in 512-sample windows
        and return the maximum probability across all windows.

        Args:
            chunk: float32 array, 16 kHz mono

        Returns:
            float: speech probability 0.0-1.0
        """
        self._ensure_session()

        if len(chunk) == 0:
            return 0.0

        chunk = chunk.astype(np.float32)

        # Process in 512-sample windows, return max probability
        max_prob = 0.0
        n = len(chunk)

        # Process full 512-sample windows
        for start in range(0, n - _SILERO_CHUNK + 1, _SILERO_CHUNK):
            window = chunk[start:start + _SILERO_CHUNK]
            prob = self._run_window(window)
            if prob > max_prob:
                max_prob = prob

        # If there's a trailing partial window, pad it with zeros
        remainder = n % _SILERO_CHUNK
        if remainder > 0 and n >= _SILERO_CHUNK:
            pass  # Already processed full windows
        elif remainder > 0:
            # Only partial data: pad to 512
            padded = np.zeros(_SILERO_CHUNK, dtype=np.float32)
            padded[:remainder] = chunk[:remainder]
            prob = self._run_window(padded)
            if prob > max_prob:
                max_prob = prob

        return round(max_prob, 3)

    def _run_window(self, window: np.ndarray) -> float:
        """Run a single 512-sample window through the model."""
        # Input shape: (1, chunk_size) — batch of 1
        x = window.reshape(1, -1)
        sr = np.array(self._sr, dtype=np.int64)

        result = self._session.run(
            None,
            {
                "input": x,
                "state": self._state,
                "sr": sr,
            }
        )
        # result[0] = output probability (shape [1,1])
        # result[1] = updated state (shape [2,1,128])
        prob = float(result[0].item())
        self._state = result[1]
        return prob

    def is_speech(self, chunk: np.ndarray) -> bool:
        """Convenience: True if probability exceeds threshold."""
        return self.probability(chunk) > self._threshold

    @property
    def loaded(self) -> bool:
        return self._session is not None
