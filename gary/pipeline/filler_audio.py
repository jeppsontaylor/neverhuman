"""
pipeline/filler_audio.py — Pre-baked WAV ack phrases for low-latency voice UX

Drop Float32 WAV files (same format as Kokoro TTS: mono, 24kHz ideal) into:
  static/audio/fillers/

Any *.wav in that directory is eligible. One file is chosen at random per utterance.
If the directory is empty, get_filler_wav_bytes() returns None (no-op).
"""

from __future__ import annotations

import logging
import random
from pathlib import Path

log = logging.getLogger("gary.filler")

_CACHED: list[bytes] | None = None


def _filler_dir(static_root: Path) -> Path:
    return static_root / "audio" / "fillers"


def load_fillers(static_root: Path) -> list[bytes]:
    """Load all WAV byte blobs from static/audio/fillers/*.wav (cached)."""
    global _CACHED
    if _CACHED is not None:
        return _CACHED
    d = _filler_dir(static_root)
    out: list[bytes] = []
    if not d.is_dir():
        _CACHED = []
        return _CACHED
    for p in sorted(d.glob("*.wav")):
        try:
            out.append(p.read_bytes())
        except OSError as exc:
            log.warning("Skipping filler %s: %s", p, exc)
    if out:
        log.info("Loaded %d filler WAV(s) from %s", len(out), d)
    _CACHED = out
    return _CACHED


def get_filler_wav_bytes(static_root: Path) -> bytes | None:
    """Return random pre-baked filler audio, or None if none configured."""
    pool = load_fillers(static_root)
    if not pool:
        return None
    return random.choice(pool)


def invalidate_cache() -> None:
    """Test hook: clear cached filler list."""
    global _CACHED
    _CACHED = None
