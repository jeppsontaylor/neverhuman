"""
pipeline/asr.py — Singleton Qwen3-ASR session for GARY

Loads Qwen3-ASR-0.6B on first use (Metal/MLX). Automatically unloads
after IDLE_TIMEOUT_SEC seconds of inactivity to free unified memory.

SSD-streaming note
------------------
The flash-moe LLM is a Mixture-of-Experts model whose expert weights (18 GB)
stream per-token from SSD via pread(). That design is specific to sparse MoE
architectures. Qwen3-ASR-0.6B is a dense transformer (0.6 B params, ~1.2 GB),
so it cannot use the same pread-streaming trick. However, MLX itself uses
memory-mapped weights internally, so the model pages are loaded lazily from
the HuggingFace cache on SSD and can be reclaimed by the OS under pressure.

The real memory win here is lazy loading + idle unloading, which is what
this module implements.

Public API:
    await transcribe(audio_np: np.ndarray) -> str
    unload()   — free model immediately (called by server idle-unload task)
    is_loaded() -> bool
    last_use_time() -> float  — monotonic seconds
"""

import asyncio
import functools
import gc
import logging
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

log = logging.getLogger("gary.asr")

_MODEL_NAME      = "Qwen/Qwen3-ASR-0.6B"
_IDLE_TIMEOUT_SEC = 300       # unload after 5 min idle (configurable by server)
_session          = None      # mlx_qwen3_asr.Session
_executor         = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gary-asr")
_last_use: float  = 0.0       # monotonic timestamp of last transcribe() call
_load_lock        = asyncio.Lock()  # prevents double-load from concurrent requests


def _sync_load() -> None:
    """Blocking model load — runs in the executor thread."""
    global _session
    if _session is not None:
        return  # already loaded (race guard inside thread)
    log.info(f"Loading ASR model (lazy): {_MODEL_NAME}")
    from mlx_qwen3_asr import Session  # type: ignore
    _session = Session(model=_MODEL_NAME)
    log.info("ASR model ready ✓")


def _sync_transcribe(audio_np: np.ndarray) -> str:
    if _session is None:
        raise RuntimeError("ASR session not loaded — this is a bug; load() should have been called first")
    result = _session.transcribe(audio_np)
    return (result.text or "").strip()


async def transcribe(audio_np: np.ndarray) -> str:
    """
    Async wrapper. Lazy-loads the model on first call, then offloads to
    the ASR ThreadPoolExecutor (model is not re-entrant).
    audio_np: float32 array, 16 kHz mono.
    Returns the transcription string.
    """
    global _last_use

    # Lazy load under asyncio lock to prevent concurrent double-loads.
    async with _load_lock:
        if _session is None:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(_executor, functools.partial(_sync_load))

    _last_use = time.monotonic()
    loop = asyncio.get_event_loop()
    text = await loop.run_in_executor(_executor, _sync_transcribe, audio_np)
    log.debug(f"ASR: {text!r}")
    return text


def unload() -> None:
    """
    Free the ASR model immediately. Safe to call at any time.
    Called by the server idle-unload background task.
    """
    global _session
    if _session is None:
        return
    log.info("ASR model unloading (idle timeout)…")
    _session = None
    gc.collect()
    # Release MLX Metal cache (frees GPU-side weight buffers from unified memory).
    try:
        import mlx.core as mx  # type: ignore
        mx.metal.clear_cache()
        log.info("ASR model unloaded · MLX Metal cache cleared ✓")
    except Exception as e:
        log.debug(f"MLX cache clear skipped: {e}")


def is_loaded() -> bool:
    return _session is not None


def last_use_time() -> float:
    """Return monotonic timestamp of last transcribe() call (0 if never used)."""
    return _last_use


def load() -> None:
    """
    Legacy synchronous load used by old startup path.
    Now a no-op: loading happens lazily on first transcribe() call.
    Kept for API compatibility.
    """
    pass  # intentional — startup no longer pre-loads models
