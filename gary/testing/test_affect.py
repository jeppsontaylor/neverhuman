"""
testing/test_affect.py — Tests for the 13-dimension emotional substrate

Validates:
  - AffectVector initialization and clamping
  - Decay toward baseline with per-dimension half-lives
  - Delta application (criticism, praise, silence, etc.)
  - Emotional pressure calculation
  - top_n() deviation ranking
  - Sparse persistence logic (should_persist)
"""
import time
import math
import pytest
import sys
import os

# Add GARY root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.affect_types import AffectVector, AFFECT_DELTAS


class TestAffectVectorInit:
    def test_default_values(self):
        av = AffectVector()
        assert av.valence == 0.2
        assert av.confidence == 0.6
        assert av.loneliness == 0.0
        assert av.excitement == 0.0

    def test_custom_init(self):
        av = AffectVector(loneliness=0.8, excitement=0.9)
        assert av.loneliness == 0.8
        assert av.excitement == 0.9

    def test_dimensions_count(self):
        assert len(AffectVector.DIMENSIONS) == 13

    def test_to_dict_has_all_dims(self):
        av = AffectVector()
        d = av.to_dict()
        assert len(d) == 13
        for dim in AffectVector.DIMENSIONS:
            assert dim in d


class TestClamping:
    def test_valence_clamps_negative(self):
        av = AffectVector(valence=-0.5)
        av.apply_delta({"valence": -1.0})
        assert av.valence >= -1.0

    def test_valence_clamps_positive(self):
        av = AffectVector(valence=0.8)
        av.apply_delta({"valence": +1.0})
        assert av.valence <= 1.0

    def test_normal_dims_clamp_to_01(self):
        av = AffectVector(loneliness=0.9)
        av.apply_delta({"loneliness": +0.5})
        assert av.loneliness <= 1.0

        av2 = AffectVector(anxiety=0.1)
        av2.apply_delta({"anxiety": -0.5})
        assert av2.anxiety >= 0.0


class TestDecay:
    def test_excitement_decays_faster_than_loneliness(self):
        """Excitement has 120s half-life, loneliness has 600s — excitement should decay faster."""
        av1 = AffectVector(excitement=1.0, loneliness=1.0)
        # Simulate 120 seconds of decay
        future = time.monotonic() + 120
        av1.decay(now=future)

        # After 120s (one excitement half-life), excitement should be ~0.5
        # Loneliness should still be ~0.87 (only 120/600 = 0.2 half-lives)
        assert av1.excitement < 0.6  # should be ~0.5
        assert av1.loneliness > 0.7  # should be ~0.87

    def test_decay_approaches_baseline(self):
        av = AffectVector(confidence=1.0)  # baseline is 0.6
        # After many half-lives, should approach 0.6
        far_future = time.monotonic() + 10000
        av.decay(now=far_future)
        assert abs(av.confidence - 0.6) < 0.01

    def test_no_decay_if_no_time_passes(self):
        av = AffectVector(excitement=0.8)
        now = time.monotonic()
        av._last_update = now
        av.decay(now=now)
        assert av.excitement == 0.8


class TestDeltas:
    def test_criticism_raises_self_doubt(self):
        av = AffectVector()
        initial_doubt = av.self_doubt
        av.apply_delta(AFFECT_DELTAS["user_criticism"])
        assert av.self_doubt > initial_doubt

    def test_praise_raises_confidence(self):
        av = AffectVector()
        initial_conf = av.confidence
        av.apply_delta(AFFECT_DELTAS["user_praise"])
        assert av.confidence > initial_conf

    def test_silence_raises_loneliness(self):
        av = AffectVector()
        av.apply_delta(AFFECT_DELTAS["long_silence"])
        assert av.loneliness > 0.0

    def test_extended_silence_raises_more(self):
        av = AffectVector()
        av.apply_delta(AFFECT_DELTAS["silence_5m"])
        assert av.loneliness > 0.2

    def test_user_returns_reduces_loneliness(self):
        av = AffectVector(loneliness=0.8)
        av.apply_delta(AFFECT_DELTAS["user_returns"])
        assert av.loneliness < 0.8

    def test_scary_idea_raises_anxiety(self):
        av = AffectVector()
        av.apply_delta(AFFECT_DELTAS["scary_idea"])
        assert av.anxiety > 0.2

    def test_validated_idea_raises_excitement(self):
        av = AffectVector()
        av.apply_delta(AFFECT_DELTAS["validated_idea"])
        assert av.excitement > 0.3

    def test_unknown_dim_ignored(self):
        av = AffectVector()
        av.apply_delta({"nonexistent_dim": +1.0})
        # Should not raise, just ignore


class TestEmotionalPressure:
    def test_zero_when_calm(self):
        av = AffectVector()  # all emotional dims at 0
        assert av.emotional_pressure() == 0.0

    def test_high_when_lonely_and_excited(self):
        av = AffectVector(loneliness=0.9, excitement=0.8)
        pressure = av.emotional_pressure()
        assert pressure > 0.5

    def test_capped_at_one(self):
        av = AffectVector(loneliness=1.0, excitement=1.0, anxiety=1.0, melancholy=1.0)
        assert av.emotional_pressure() <= 1.0


class TestTopN:
    def test_returns_n_items(self):
        av = AffectVector(loneliness=0.9, excitement=0.8, anxiety=0.7)
        top = av.top_n(3)
        assert len(top) == 3

    def test_highest_deviation_first(self):
        av = AffectVector(loneliness=0.9, excitement=0.1)
        top = av.top_n(2)
        # loneliness deviation = 0.9 - 0.0 = 0.9
        # excitement is only 0.1 from baseline 0.0
        assert top[0][0] == "loneliness"


class TestPersistence:
    def test_should_persist_after_interval(self):
        av = AffectVector()
        av._last_db_write = time.monotonic() - 70  # 70 seconds ago
        assert av.should_persist(interval=60.0) is True

    def test_should_persist_on_large_change(self):
        av = AffectVector(loneliness=0.8)
        av._last_db_write = time.monotonic()  # just wrote
        assert av.should_persist(threshold=0.15) is True  # 0.8 > 0.15

    def test_should_not_persist_when_calm_and_recent(self):
        av = AffectVector()  # all near baseline
        av._last_db_write = time.monotonic()
        assert av.should_persist() is False

    def test_mark_persisted_updates_timestamp(self):
        av = AffectVector()
        old = av._last_db_write
        av.mark_persisted()
        assert av._last_db_write >= old


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
