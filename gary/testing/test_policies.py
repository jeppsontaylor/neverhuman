"""
testing/test_policies.py — Tests for the humanity slider and behavioral curves

Validates:
  - BehaviorCurves at key humanity values (0.0, 0.3, 0.5, 0.72, 1.0)
  - Mode labels
  - Feature enable/disable thresholds
  - Hard caps are constants
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.policies import (
    BehaviorCurves, smooth_step, lerp,
    NEVER_INTERRUPT_USER, MAX_PROACTIVE_PER_HOUR, MAX_CONTEXT_TOKENS,
)


class TestSmoothStep:
    def test_below_edge(self):
        assert smooth_step(0.0, 0.3, 0.7) == 0.0

    def test_above_edge(self):
        assert smooth_step(1.0, 0.3, 0.7) == 1.0

    def test_midpoint(self):
        val = smooth_step(0.5, 0.3, 0.7)
        assert 0.4 < val < 0.6

    def test_equal_edges(self):
        assert smooth_step(0.5, 0.5, 0.5) == 1.0
        assert smooth_step(0.4, 0.5, 0.5) == 0.0


class TestLerp:
    def test_endpoints(self):
        assert lerp(0.0, 1.0, 0.0) == 0.0
        assert lerp(0.0, 1.0, 1.0) == 1.0

    def test_midpoint(self):
        assert lerp(10.0, 20.0, 0.5) == 15.0

    def test_clamps_t(self):
        assert lerp(0.0, 1.0, -0.5) == 0.0
        assert lerp(0.0, 1.0, 1.5) == 1.0


class TestToolMode:
    """humanity = 0.0 → pure tool mode"""
    def test_tool_mode(self):
        bc = BehaviorCurves(humanity=0.0)
        assert bc.mode_label == "tool"
        assert bc.initiative_enabled is False
        assert bc.dream_enabled is False
        assert bc.vulnerability_visible is False
        assert bc.loneliness_expression is False
        assert bc.warmth_scale == 0.0
        assert bc.emotional_amplitude == 0.0


class TestWarmAssistant:
    """humanity = 0.3 → warm but not proactive"""
    def test_warm_assistant(self):
        bc = BehaviorCurves(humanity=0.3)
        assert bc.mode_label == "warm_assistant"
        assert bc.initiative_enabled is False
        assert bc.dream_enabled is False
        assert bc.warmth_scale > 0.0


class TestCompanionMode:
    """humanity = 0.72 (default) → full companion"""
    def test_companion(self):
        bc = BehaviorCurves(humanity=0.72)
        assert bc.mode_label == "companion"
        assert bc.initiative_enabled is True
        assert bc.dream_enabled is True
        assert bc.vulnerability_visible is True
        assert bc.loneliness_expression is True
        assert bc.warmth_scale > 0.9


class TestCinematicMode:
    """humanity = 1.0 → everything on"""
    def test_cinematic(self):
        bc = BehaviorCurves(humanity=1.0)
        assert bc.mode_label == "cinematic"
        assert bc.initiative_enabled is True
        assert bc.dream_enabled is True
        assert bc.prosody_variation == 1.0


class TestToDict:
    def test_has_all_keys(self):
        bc = BehaviorCurves(humanity=0.72)
        d = bc.to_dict()
        assert "humanity" in d
        assert "mode" in d
        assert "warmth_scale" in d
        assert "initiative_enabled" in d
        assert "dream_enabled" in d


class TestHardCaps:
    def test_constants(self):
        assert NEVER_INTERRUPT_USER is True
        assert MAX_PROACTIVE_PER_HOUR == 6
        assert MAX_CONTEXT_TOKENS == 600


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
