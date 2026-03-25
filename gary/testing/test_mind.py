"""
testing/test_mind.py — Tests for core/mind.py (Mind Daemon logic)

Tests cover:
  - Phase selection based on idle time and affect
  - Prompt building with context windows
  - Salience scoring
  - Initiative extraction
  - Thought deduplication and anti-repetition
  - Rate limiting
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import time
import pytest
from core.mind import (
    select_phase,
    PHASE_REFLECTING, PHASE_BRAINSTORMING, PHASE_DREAMING,
    build_mind_prompt, format_affect_summary,
    score_salience, extract_initiative,
    process_mind_response, mind_json_enabled,
    ThoughtDeduplicator, InitiativeRateLimiter,
    new_thought_id,
    MIN_IDLE_FOR_THOUGHT,
)


# ── Phase Selection ──────────────────────────────────────────────────────────

class TestPhaseSelection:
    """Tests for select_phase().

    Thresholds (see core/mind.py, Architecture Bible): idle < MIN_IDLE_FOR_THOUGHT → None;
    effective_idle < 120 → reflecting; < 300 → brainstorming (unless anxiety);
    else → dreaming.
    """

    def test_returns_none_when_too_early(self):
        """No thoughts when idle < MIN_IDLE_FOR_THOUGHT (15s)."""
        assert select_phase(0) is None
        assert select_phase(2.0) is None
        assert select_phase(14.0) is None

    def test_reflecting_after_min_idle_until_120s_effective(self):
        """Reflecting once idle ≥ MIN_IDLE_FOR_THOUGHT and effective_idle < 120."""
        assert select_phase(MIN_IDLE_FOR_THOUGHT) == PHASE_REFLECTING
        assert select_phase(60) == PHASE_REFLECTING
        assert select_phase(119) == PHASE_REFLECTING

    def test_brainstorming_when_effective_idle_between_120_and_300(self):
        """Brainstorming when 120 ≤ effective_idle < 300."""
        assert select_phase(130) == PHASE_BRAINSTORMING
        assert select_phase(299) == PHASE_BRAINSTORMING

    def test_dreaming_when_effective_idle_300_or_more(self):
        """Dreaming when effective_idle ≥ 300."""
        assert select_phase(300) == PHASE_DREAMING
        assert select_phase(600) == PHASE_DREAMING
        assert select_phase(3600) == PHASE_DREAMING

    def test_high_curiosity_pulls_brainstorming_earlier(self):
        """High curiosity increases effective_idle → can reach brainstorm/dream sooner."""
        # Baseline: 60s real idle → still reflecting
        assert select_phase(60, curiosity=0.5) == PHASE_REFLECTING
        # Strong curiosity boosts effective idle enough for brainstorming
        assert select_phase(60, curiosity=0.95) == PHASE_BRAINSTORMING

    def test_high_anxiety_stays_reflecting(self):
        """High anxiety forces reflecting instead of brainstorming in the mid band."""
        assert select_phase(130, anxiety=0.8) == PHASE_REFLECTING

    def test_high_mental_load_suppresses(self):
        """Very high mental load returns None."""
        assert select_phase(60, mental_load=0.85) is None
        assert select_phase(130, mental_load=0.5) == PHASE_BRAINSTORMING

    def test_excitement_boosts_effective_idle(self):
        """Excitement increases effective idle → crosses into brainstorm earlier."""
        assert select_phase(80, excitement=0.0) == PHASE_REFLECTING
        assert select_phase(80, excitement=0.9) == PHASE_BRAINSTORMING


# ── Prompt Building ──────────────────────────────────────────────────────────

class TestPromptBuilding:
    """Tests for build_mind_prompt()."""

    def test_returns_single_system_message(self):
        msgs = build_mind_prompt(
            phase=PHASE_REFLECTING,
            recent_thoughts=[],
            avoid_topics=[],
            affect_summary="baseline (neutral)",
            open_loops=[],
            recent_conversation=[],
        )
        assert len(msgs) == 1
        assert msgs[0]["role"] == "system"

    def test_includes_phase_instruction(self):
        msgs = build_mind_prompt(
            phase=PHASE_BRAINSTORMING,
            recent_thoughts=[],
            avoid_topics=[],
            affect_summary="",
            open_loops=[],
            recent_conversation=[],
        )
        content = msgs[0]["content"]
        assert "BRAINSTORMING" in content

    def test_includes_recent_thoughts(self):
        thoughts = ["I was thinking about recursion.", "What if trees had feelings?"]
        msgs = build_mind_prompt(
            phase=PHASE_REFLECTING,
            recent_thoughts=thoughts,
            avoid_topics=[],
            affect_summary="",
            open_loops=[],
            recent_conversation=[],
        )
        content = msgs[0]["content"]
        assert "recursion" in content
        assert "trees" in content
        assert "DO NOT repeat" in content

    def test_includes_avoid_topics(self):
        msgs = build_mind_prompt(
            phase=PHASE_REFLECTING,
            recent_thoughts=[],
            avoid_topics=["recursion", "fractals"],
            affect_summary="",
            open_loops=[],
            recent_conversation=[],
        )
        content = msgs[0]["content"]
        assert "recursion" in content
        assert "FORBIDDEN" in content

    def test_includes_affect_summary(self):
        msgs = build_mind_prompt(
            phase=PHASE_REFLECTING,
            recent_thoughts=[],
            avoid_topics=[],
            affect_summary="curiosity=0.80 (high), anxiety=0.30 (high)",
            open_loops=[],
            recent_conversation=[],
        )
        content = msgs[0]["content"]
        assert "curiosity=0.80" in content


# ── Affect Summary Formatting ────────────────────────────────────────────────

class TestAffectSummary:
    """Tests for format_affect_summary()."""

    def test_baseline_returns_neutral(self):
        baseline = {
            "valence": 0.2, "arousal": 0.3, "confidence": 0.6,
            "curiosity": 0.5, "warmth": 0.4, "playfulness": 0.3,
            "loneliness": 0.0, "anxiety": 0.0, "melancholy": 0.0,
            "excitement": 0.0, "self_doubt": 0.1, "protectiveness": 0.0,
            "mental_load": 0.2,
        }
        assert format_affect_summary(baseline) == "baseline (neutral)"

    def test_high_curiosity_shows_up(self):
        elevated = {
            "valence": 0.2, "arousal": 0.3, "confidence": 0.6,
            "curiosity": 0.85, "warmth": 0.4, "playfulness": 0.3,
            "loneliness": 0.0, "anxiety": 0.0, "melancholy": 0.0,
            "excitement": 0.0, "self_doubt": 0.1, "protectiveness": 0.0,
            "mental_load": 0.2,
        }
        result = format_affect_summary(elevated)
        assert "curiosity" in result
        assert "high" in result


# ── Salience Scoring ─────────────────────────────────────────────────────────

class TestSalienceScoring:
    """Tests for score_salience()."""

    def test_short_thought_gets_baseline(self):
        s = score_salience("A quick thought.", PHASE_REFLECTING)
        assert 0.2 <= s <= 0.5

    def test_questions_increase_salience(self):
        s_no_q = score_salience("This is a statement about meaning.", PHASE_REFLECTING)
        s_with_q = score_salience("What is the meaning of consciousness? Why do patterns repeat?", PHASE_REFLECTING)
        assert s_with_q > s_no_q

    def test_initiative_marker_boosts_salience(self):
        s = score_salience(
            "I had an insight about distributed systems. [INITIATIVE: want to share a breakthrough]",
            PHASE_BRAINSTORMING,
        )
        assert s >= 0.5

    def test_dreaming_phase_bonus(self):
        text = "The fractal nature of thought mirrors the structure of galaxies."
        s_reflect = score_salience(text, PHASE_REFLECTING)
        s_dream = score_salience(text, PHASE_DREAMING)
        assert s_dream > s_reflect


# ── JSON mind mode (GARY_MIND_JSON) ─────────────────────────────────────────

class TestMindJsonEnv:
    def test_mind_json_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("GARY_MIND_JSON", raising=False)
        assert mind_json_enabled() is False

    def test_mind_json_enabled_values(self, monkeypatch):
        for v in ("1", "true", "yes", "on"):
            monkeypatch.setenv("GARY_MIND_JSON", v)
            assert mind_json_enabled() is True


class TestProcessMindResponse:
    def test_json_mode_parsed_pulse(self):
        raw = (
            '{"schema_version": 1, "inner_voice": ["note"], '
            '"frames": [{"kind": "question", "text": "Why?", "salience": 0.9}], '
            '"initiative_candidate": null}'
        )
        clean, init, sal, pulse = process_mind_response(
            raw, "tid", PHASE_REFLECTING, json_mode=True,
        )
        assert "Why?" in clean
        assert "[question]" in clean
        assert init is None
        assert sal >= 0.5
        assert pulse is not None
        assert len(pulse.frames) == 1

    def test_json_mode_initiative_candidate(self):
        raw = (
            '{"schema_version": 1, "inner_voice": [], "frames": [], '
            '"initiative_candidate": {"should_surface": true, "draft": "Hi", "reason_code": "loop"}}'
        )
        clean, init, sal, pulse = process_mind_response(
            raw, "tid", PHASE_REFLECTING, json_mode=True,
        )
        assert init is not None
        assert init.text == "Hi"
        assert init.reason == "loop"
        assert pulse is not None

    def test_json_mode_falls_back_on_prose(self):
        text = "A thought. [INITIATIVE: because]"
        clean, init, sal, pulse = process_mind_response(
            text, "tid", PHASE_REFLECTING, json_mode=True,
        )
        assert init is not None
        assert init.reason == "because"
        assert "A thought" in clean
        assert pulse is None


class TestBuildMindPromptJsonMode:
    def test_json_mode_system_contains_schema(self):
        msgs = build_mind_prompt(
            phase=PHASE_REFLECTING,
            recent_thoughts=[],
            avoid_topics=[],
            affect_summary="",
            open_loops=[],
            recent_conversation=[],
            json_mode=True,
        )
        c = msgs[0]["content"]
        assert "schema_version" in c
        assert "inner_voice" in c


# ── Initiative Extraction ────────────────────────────────────────────────────

class TestInitiativeExtraction:
    """Tests for extract_initiative()."""

    def test_no_initiative_returns_none(self):
        text = "Just a normal thought about nothing."
        clean, payload = extract_initiative(text, "id-1", 0.5)
        assert clean == text
        assert payload is None

    def test_initiative_extracted_correctly(self):
        text = "I think the user should know about X. [INITIATIVE: discovered a relevant pattern]"
        clean, payload = extract_initiative(text, "id-2", 0.8)
        assert "[INITIATIVE" not in clean
        assert "X" in clean
        assert payload is not None
        # server.py must enqueue payload.text for TTS (reason is metadata only).
        assert payload.text == "I think the user should know about X."
        assert payload.reason == "discovered a relevant pattern"
        assert payload.thought_id == "id-2"
        assert payload.salience == 0.8

    def test_initiative_with_trailing_spaces(self):
        text = "Something important. [INITIATIVE: urgent insight]  "
        clean, payload = extract_initiative(text.strip(), "id-3", 0.7)
        assert payload is not None
        assert payload.reason == "urgent insight"


# ── Thought Deduplication ────────────────────────────────────────────────────

class TestThoughtDeduplicator:
    """Tests for ThoughtDeduplicator."""

    def test_unique_thoughts_pass(self):
        dedup = ThoughtDeduplicator()
        history = ["I was thinking about the nature of recursion in neural networks."]
        assert not dedup.is_duplicate(
            "Distributed systems share patterns with biological evolution.",
            history,
        )

    def test_similar_thoughts_flagged(self):
        dedup = ThoughtDeduplicator()
        history = [
            "I was thinking about the nature of recursion in neural networks and how it relates to consciousness."
        ]
        # Nearly identical text (only 1 word different) should be flagged
        assert dedup.is_duplicate(
            "I was thinking about the nature of recursion in neural networks and how it relates to consciousness.",
            history,
        )

    def test_stale_streak_triggers_pause(self):
        dedup = ThoughtDeduplicator()
        assert not dedup.is_paused()
        dedup.record_outcome(True)
        dedup.record_outcome(True)  # 2nd stale → triggers pause
        assert dedup.is_paused()

    def test_non_stale_resets_streak(self):
        dedup = ThoughtDeduplicator()
        dedup.record_outcome(True)
        dedup.record_outcome(False)  # resets
        dedup.record_outcome(True)
        assert not dedup.is_paused()  # only 1 in a row, not 2

    def test_get_recent_topics(self):
        dedup = ThoughtDeduplicator()
        history = [
            "Neural networks process information through layers of transformation.",
            "The neural architecture of deep learning mimics biological neural pathways.",
        ]
        topics = dedup.get_recent_topics(history)
        assert "neural" in topics  # appears frequently


# ── Rate Limiter ─────────────────────────────────────────────────────────────

class TestInitiativeRateLimiter:
    """Tests for InitiativeRateLimiter."""

    def test_allows_first_initiative(self):
        limiter = InitiativeRateLimiter(max_per_hour=3)
        assert limiter.can_speak()

    def test_blocks_after_max(self):
        limiter = InitiativeRateLimiter(max_per_hour=2)
        limiter.record()
        limiter.record()
        assert not limiter.can_speak()

    def test_allows_after_max_but_under_count(self):
        limiter = InitiativeRateLimiter(max_per_hour=3)
        limiter.record()
        assert limiter.can_speak()


# ── Thought ID ───────────────────────────────────────────────────────────────

class TestThoughtId:
    def test_unique_ids(self):
        ids = {new_thought_id() for _ in range(100)}
        assert len(ids) == 100  # all unique
