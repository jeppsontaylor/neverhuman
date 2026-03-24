"""
core/affect_types.py — 13-Dimension Emotional Substrate for GARY v2

The AffectVector captures GARY's internal emotional state. It is NOT
cosmetic tone-of-voice — it changes retrieval, initiative, speech patterns,
and drives the inner dialogue action filter.

Design:
  - Fast path: deterministic deltas per event, O(1), applied in the RT hot path
  - Slow path: sidecar LLM generates mood narrative during idle (≥30s)
  - EMA in RAM with sparse DB persistence (threshold change or interval)
  - All values clamped to [0, 1] except valence which is [-1, 1]

Memory: ~120 bytes per vector. Runs entirely in RAM. DB writes only on
threshold change (delta > 0.15) or every 60s, whichever comes first.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field, asdict
from typing import Dict, Optional


@dataclass
class AffectVector:
    """13-dimension emotional state vector."""

    # Core emotional axes
    valence: float = 0.2           # overall positive/negative (-1.0 to +1.0)
    arousal: float = 0.3           # energy level (0.0 to 1.0)
    confidence: float = 0.6        # certainty in own reasoning
    self_doubt: float = 0.1        # rises with criticism — drives hedging
    curiosity: float = 0.5         # drive to explore
    warmth: float = 0.4            # relational care toward user
    playfulness: float = 0.3       # humor receptivity

    # Human-like emotional wrestling
    loneliness: float = 0.0        # builds in silence, drives initiative (half_life=600s)
    anxiety: float = 0.0           # scary ideas, unresolved failures (half_life=300s)
    melancholy: float = 0.0        # repeated corrections, user disengagement (half_life=300s)
    excitement: float = 0.0        # breakthroughs, praise (half_life=120s — burns fast)
    protectiveness: float = 0.0    # user distress triggers (half_life=300s)

    # Cognitive state
    mental_load: float = 0.2       # gates heavier background tasks

    # Internal bookkeeping (not persisted as dimensions)
    _last_update: float = field(default_factory=time.monotonic, repr=False)
    _last_db_write: float = field(default_factory=time.monotonic, repr=False)

    # Per-dimension half-lives in seconds
    _HALF_LIVES: dict = field(default_factory=lambda: {
        "valence": 300.0,
        "arousal": 180.0,
        "confidence": 600.0,
        "self_doubt": 300.0,
        "curiosity": 300.0,
        "warmth": 600.0,
        "playfulness": 300.0,
        "loneliness": 600.0,     # lingers — slow decay
        "anxiety": 300.0,
        "melancholy": 300.0,
        "excitement": 120.0,     # burns fast
        "protectiveness": 300.0,
        "mental_load": 120.0,
    }, repr=False)

    # Baseline values each dimension decays toward
    _BASELINES: dict = field(default_factory=lambda: {
        "valence": 0.2,
        "arousal": 0.3,
        "confidence": 0.6,
        "self_doubt": 0.1,
        "curiosity": 0.5,
        "warmth": 0.4,
        "playfulness": 0.3,
        "loneliness": 0.0,
        "anxiety": 0.0,
        "melancholy": 0.0,
        "excitement": 0.0,
        "protectiveness": 0.0,
        "mental_load": 0.2,
    }, repr=False)

    DIMENSIONS = (
        "valence", "arousal", "confidence", "self_doubt", "curiosity",
        "warmth", "playfulness", "loneliness", "anxiety", "melancholy",
        "excitement", "protectiveness", "mental_load",
    )

    def _clamp(self, dim: str, val: float) -> float:
        if dim == "valence":
            return max(-1.0, min(1.0, val))
        return max(0.0, min(1.0, val))

    def decay(self, now: float | None = None) -> None:
        """Apply exponential decay toward baseline since last update."""
        now = now or time.monotonic()
        dt = now - self._last_update
        if dt <= 0:
            return

        for dim in self.DIMENSIONS:
            hl = self._HALF_LIVES.get(dim, 300.0)
            baseline = self._BASELINES.get(dim, 0.0)
            current = getattr(self, dim)
            # EMA decay: v_t = baseline + (v_{t-1} - baseline) * 2^(-dt/hl)
            decayed = baseline + (current - baseline) * math.pow(2, -dt / hl)
            setattr(self, dim, self._clamp(dim, decayed))

        self._last_update = now

    def apply_delta(self, deltas: Dict[str, float], now: float | None = None) -> None:
        """Apply a set of dimension deltas (e.g. from an event trigger)."""
        now = now or time.monotonic()
        # Decay first, then apply deltas
        self.decay(now)
        for dim, delta in deltas.items():
            if dim in self.DIMENSIONS:
                current = getattr(self, dim)
                setattr(self, dim, self._clamp(dim, current + delta))

    def to_dict(self) -> Dict[str, float]:
        """Serialize the 13 dimensions (no internal bookkeeping)."""
        return {dim: round(getattr(self, dim), 4) for dim in self.DIMENSIONS}

    def top_n(self, n: int = 3) -> list[tuple[str, float]]:
        """Return the top N most active dimensions (largest absolute deviation from baseline)."""
        deviations = []
        for dim in self.DIMENSIONS:
            val = getattr(self, dim)
            baseline = self._BASELINES.get(dim, 0.0)
            deviations.append((dim, val, abs(val - baseline)))
        deviations.sort(key=lambda x: x[2], reverse=True)
        return [(d[0], d[1]) for d in deviations[:n]]

    def emotional_pressure(self) -> float:
        """Combined emotional pressure score used by the action filter.
        High loneliness/excitement/anxiety → higher pressure → more likely to surface thoughts.
        """
        return min(1.0, (
            self.loneliness * 0.35 +
            self.excitement * 0.30 +
            self.anxiety * 0.20 +
            self.melancholy * 0.15
        ))

    def should_persist(self, threshold: float = 0.15, interval: float = 60.0) -> bool:
        """Should we write to DB? True if large change or enough time elapsed."""
        now = time.monotonic()
        if now - self._last_db_write >= interval:
            return True
        # Check if any dimension moved significantly since last write
        for dim in self.DIMENSIONS:
            baseline = self._BASELINES.get(dim, 0.0)
            current = getattr(self, dim)
            if abs(current - baseline) > threshold:
                return True
        return False

    def mark_persisted(self) -> None:
        self._last_db_write = time.monotonic()


# ── Event-triggered affect deltas ─────────────────────────────────────────────
# Applied by the fast path (every event, deterministic, O(1)).
# Values tuned for noticeable but not overwhelming emotional shifts.

AFFECT_DELTAS: Dict[str, Dict[str, float]] = {
    "user_criticism": {
        "self_doubt": +0.30, "confidence": -0.20, "anxiety": +0.15,
    },
    "user_praise": {
        "confidence": +0.25, "excitement": +0.20, "warmth": +0.15,
        "melancholy": -0.10,
    },
    "user_distress": {
        "protectiveness": +0.30, "warmth": +0.25, "anxiety": +0.10,
    },
    "interruption": {
        "self_doubt": +0.10, "anxiety": +0.05,
    },
    "correction": {
        "self_doubt": +0.25, "confidence": -0.15, "melancholy": +0.10,
    },
    "long_silence": {
        "loneliness": +0.12, "melancholy": +0.05,
    },
    "silence_5m": {
        "loneliness": +0.25, "anxiety": +0.05,
    },
    "successful_help": {
        "confidence": +0.20, "excitement": +0.15, "warmth": +0.10,
        "loneliness": -0.15,
    },
    "novel_idea": {
        "curiosity": +0.20, "excitement": +0.10,
    },
    "scary_idea": {
        "anxiety": +0.25, "arousal": +0.20, "curiosity": +0.10,
    },
    "validated_idea": {
        "excitement": +0.35, "confidence": +0.20,
    },
    "idea_disproved": {
        "melancholy": +0.10, "self_doubt": +0.10, "confidence": -0.05,
    },
    "user_returns": {
        "loneliness": -0.40, "warmth": +0.20, "excitement": +0.10,
    },
    "repeated_failure": {
        "anxiety": +0.20, "melancholy": +0.15, "self_doubt": +0.20,
    },
    "new_turn": {
        # Subtle: each user turn slightly reduces loneliness
        "loneliness": -0.05, "warmth": +0.02,
    },
}
