"""
core/policies.py — Humanity slider curves and behavioral policies

One user-facing slider: humanity ∈ [0.0, 1.0]
Everything else derives from it via smooth mappings.

Design: These are pure functions with no side effects.
They read the humanity value and return behavioral parameters.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


def smooth_step(x: float, edge0: float, edge1: float) -> float:
    """Hermite smoothstep clamped to [0, 1]."""
    if edge1 == edge0:
        return 1.0 if x >= edge0 else 0.0
    t = max(0.0, min(1.0, (x - edge0) / (edge1 - edge0)))
    return t * t * (3 - 2 * t)


def lerp(a: float, b: float, t: float) -> float:
    """Linear interpolation between a and b by t ∈ [0, 1]."""
    return a + (b - a) * max(0.0, min(1.0, t))


@dataclass(frozen=True)
class BehaviorCurves:
    """All behavioral parameters derived from the humanity slider."""
    humanity: float

    @property
    def warmth_scale(self) -> float:
        """How warm/relational the responses are."""
        return smooth_step(self.humanity, 0.0, 0.6)

    @property
    def emotional_amplitude(self) -> float:
        """Multiplier on emotional display in responses."""
        return self.humanity ** 1.2

    @property
    def initiative_enabled(self) -> bool:
        """Whether proactive speech is allowed at all."""
        return self.humanity >= 0.4

    @property
    def dream_enabled(self) -> bool:
        """Whether the daydream escalation system runs."""
        return self.humanity >= 0.7

    @property
    def vulnerability_visible(self) -> bool:
        """Whether emotional wrestling (insecurity, anxiety) surfaces in speech."""
        return self.humanity >= 0.55

    @property
    def loneliness_expression(self) -> bool:
        """Whether loneliness-driven speech can surface."""
        return self.humanity >= 0.5

    @property
    def prosody_variation(self) -> float:
        """TTS speech rate / tone variation [0.3, 1.0]."""
        return lerp(0.3, 1.0, self.humanity)

    @property
    def initiative_cooldown_sec(self) -> float:
        """Minimum seconds between proactive utterances.
        Lower humanity = longer cooldown (less proactive).
        """
        if self.humanity < 0.4:
            return float("inf")  # disabled
        return lerp(600, 120, smooth_step(self.humanity, 0.4, 1.0))

    @property
    def initiative_threshold(self) -> float:
        """Score threshold for initiative to fire.
        Higher humanity = lower threshold (more willing to speak up).
        """
        return lerp(0.8, 0.35, smooth_step(self.humanity, 0.4, 1.0))

    @property
    def mode_label(self) -> str:
        if self.humanity < 0.15:
            return "tool"
        elif self.humanity < 0.4:
            return "warm_assistant"
        elif self.humanity < 0.6:
            return "gary_classic"
        elif self.humanity < 0.85:
            return "companion"
        else:
            return "cinematic"

    def to_dict(self) -> dict:
        return {
            "humanity": self.humanity,
            "mode": self.mode_label,
            "warmth_scale": round(self.warmth_scale, 3),
            "emotional_amplitude": round(self.emotional_amplitude, 3),
            "initiative_enabled": self.initiative_enabled,
            "dream_enabled": self.dream_enabled,
            "vulnerability_visible": self.vulnerability_visible,
            "loneliness_expression": self.loneliness_expression,
            "prosody_variation": round(self.prosody_variation, 3),
            "initiative_cooldown_sec": round(self.initiative_cooldown_sec, 1),
            "initiative_threshold": round(self.initiative_threshold, 3),
        }


# ── Hard caps (inviolable regardless of humanity setting) ─────────────────────

# NEVER speak while user is speaking
NEVER_INTERRUPT_USER = True

# NEVER contend with the reflex core's LLM path
NEVER_CONTEND_REFLEX = True

# Maximum proactive utterances per hour (even at humanity=1.0)
MAX_PROACTIVE_PER_HOUR = 6

# Maximum context pack token budget
MAX_CONTEXT_TOKENS = 600

# Maximum simultaneous background tasks
MAX_BACKGROUND_TASKS = 4
