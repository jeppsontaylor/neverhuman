"""
pipeline/tts.py — Fast TTS via kokoro-onnx (Kokoro-82M, natural English voice)

Loads Kokoro-82M on first synthesis call (lazy). Automatically unloads after
IDLE_TIMEOUT_SEC seconds of inactivity to free unified memory.

SSD-streaming note
------------------
The flash-moe LLM streams expert weights (18 GB) per-token via pread() because
the model is a sparse MoE — only K=8 experts are needed per token, so streaming
is feasible. Kokoro-82M is a dense 82M-parameter ONNX model (~320 MB). Dense
models cannot use pread-per-token streaming, but ONNXRuntime already lazy-loads
ONNX operators from the file at session creation time. Unloading and reloading
is the practical equivalent for a model this size.

kokoro-onnx works without spacy/blis, supports Python 3.14, and produces
very natural speech at sub-100ms latency per sentence on Apple Silicon.

Model files are auto-downloaded on first run (~350 MB total):
  - kokoro-v1.0.onnx  (from github.com/thewh1teagle/kokoro-onnx releases)
  - voices-v1.0.bin

Files cached to ~/.cache/kokoro-onnx/
"""

import asyncio
import functools
import gc
import io
import logging
import os
import time
import urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import numpy as np

log = logging.getLogger("gary.tts")

_executor    = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gary-tts")
_tts_loaded  = False
_kokoro      = None
_sample_rate = 24000
_VOICE       = "af_heart"   # warm, natural female US English (changeable at runtime)
_last_use: float = 0.0      # monotonic timestamp of last synthesize() call
_tts_just_loaded = False     # one-shot flag: True after first lazy load

_CACHE_DIR   = Path.home() / ".cache" / "kokoro-onnx"
_MODEL_URL   = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
_VOICES_URL  = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"

_load_lock   = asyncio.Lock()   # prevents double-load from concurrent synthesize() calls


def _ensure_file(url: str, dest: Path) -> Path:
    if dest.exists():
        return dest
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    log.info(f"Downloading TTS model file: {url}")
    tmp = dest.with_suffix(".tmp")
    urllib.request.urlretrieve(url, tmp)
    tmp.rename(dest)
    log.info(f"Saved: {dest} ({dest.stat().st_size // 1024 // 1024}MB)")
    return dest


def _sync_load() -> None:
    """Blocking model load — runs in executor thread."""
    global _tts_loaded, _kokoro, _sample_rate, _tts_just_loaded

    if _kokoro is not None:
        return  # already loaded (race guard)

    try:
        from kokoro_onnx import Kokoro  # type: ignore

        model_path  = _ensure_file(_MODEL_URL,  _CACHE_DIR / "kokoro-v1.0.onnx")
        voices_path = _ensure_file(_VOICES_URL, _CACHE_DIR / "voices-v1.0.bin")

        log.info("Loading Kokoro-82M TTS (lazy)…")
        _kokoro = Kokoro(str(model_path), str(voices_path))
        _sample_rate = 24000

        # Warmup: pre-compile ONNX session to eliminate first-call latency spike.
        try:
            _sync_run_kokoro("Hello.")
            log.info("TTS warmup complete")
        except Exception as e:
            log.debug(f"TTS warmup (non-fatal): {e}")

        _tts_loaded = True
        log.info(f"TTS ready ✓ (Kokoro-82M, sr={_sample_rate}Hz, voice={_VOICE})")
        _tts_just_loaded = True  # signal to server to re-broadcast health

    except ImportError:
        log.warning("kokoro-onnx not installed — TTS disabled. pip install kokoro-onnx")
    except Exception as exc:
        log.warning(f"TTS load failed: {exc!r} — running in text-only mode")


# ── Voice management ──────────────────────────────────────────────────────────

def get_voices() -> list[str]:
    """Return all voices supported by the loaded model."""
    if _kokoro is None:
        return []
    try:
        return list(_kokoro.get_voices())
    except Exception:
        return []


def get_voice() -> str:
    return _VOICE


def set_voice(voice: str) -> bool:
    """Set the active voice. Returns True if valid."""
    global _VOICE
    available = get_voices()
    if voice in available:
        _VOICE = voice
        log.info(f"TTS voice changed → {voice}")
        return True
    log.warning(f"Unknown voice '{voice}', keeping '{_VOICE}'")
    return False


# ── Synthesis ─────────────────────────────────────────────────────────────────

def _sync_run_kokoro(text: str):
    samples, sr = _kokoro.create(text, voice=_VOICE, speed=1.0, lang="en-us")
    return samples.astype(np.float32), int(sr)


def _sync_synthesize(text: str) -> bytes:
    global _last_use
    if not _tts_loaded or _kokoro is None:
        return b""
    try:
        import soundfile as sf  # type: ignore

        audio_np, sr = _sync_run_kokoro(text)

        peak = np.abs(audio_np).max()
        if peak > 0:
            audio_np = audio_np / peak * 0.90

        buf = io.BytesIO()
        sf.write(buf, audio_np, sr, format="WAV", subtype="FLOAT")
        buf.seek(0)
        return buf.read()
    except Exception as exc:
        log.warning(f"TTS synthesis error: {exc}")
        return b""


async def synthesize(text: str) -> bytes:
    global _last_use

    if not text.strip():
        return b""

    # Lazy-load under asyncio lock to prevent concurrent double-loads.
    async with _load_lock:
        if _kokoro is None:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(_executor, functools.partial(_sync_load))

    _last_use = time.monotonic()
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _sync_synthesize, text)


def unload() -> None:
    """
    Free the TTS model immediately. Safe to call at any time.
    Called by the server idle-unload background task.
    """
    global _tts_loaded, _kokoro
    if _kokoro is None:
        return
    log.info("TTS model unloading (idle timeout)…")
    _kokoro = None
    _tts_loaded = False
    gc.collect()
    log.info("TTS model unloaded ✓")


def is_available() -> bool:
    return _tts_loaded


def did_just_load() -> bool:
    """Returns True once after the first lazy-load. Resets after read."""
    global _tts_just_loaded
    if _tts_just_loaded:
        _tts_just_loaded = False
        return True
    return False


def is_loaded() -> bool:
    return _kokoro is not None


def last_use_time() -> float:
    """Return monotonic timestamp of last synthesize() call (0 if never used)."""
    return _last_use


def load() -> None:
    """
    Legacy synchronous load used by old startup path.
    Now a no-op: loading happens lazily on first synthesize() call.
    Kept for API compatibility.
    """
    pass  # intentional — startup no longer pre-loads models
