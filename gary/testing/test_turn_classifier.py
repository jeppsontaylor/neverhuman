"""
testing/test_turn_classifier.py — Tests for the deterministic turn classifier

Validates SNAP/LAYERED/DEEP classification and ensures <1ms per call.
"""
from __future__ import annotations

import time

from pipeline.turn_classifier import (
    TurnMode, IntentClass, ReasoningMode, TurnClassification,
    classify_turn, classify_turn_v2,
)


class TestSnapClassification:
    """Utterances that should be classified as SNAP."""

    def test_greetings(self):
        for phrase in ["hello", "hi", "hey", "Hey GARY", "Hello GARY"]:
            assert classify_turn(phrase) == TurnMode.SNAP, f"Failed: {phrase!r}"

    def test_acknowledgments(self):
        for phrase in ["yes", "no", "yeah", "nope", "sure", "okay", "ok", "got it"]:
            assert classify_turn(phrase) == TurnMode.SNAP, f"Failed: {phrase!r}"

    def test_thanks(self):
        for phrase in ["thanks", "thank you", "Thank you GARY", "thanks gary"]:
            assert classify_turn(phrase) == TurnMode.SNAP, f"Failed: {phrase!r}"

    def test_farewells(self):
        for phrase in ["bye", "goodbye", "see you", "later", "goodnight"]:
            assert classify_turn(phrase) == TurnMode.SNAP, f"Failed: {phrase!r}"

    def test_short_commands(self):
        for phrase in ["stop", "cancel", "never mind", "forget it"]:
            assert classify_turn(phrase) == TurnMode.SNAP, f"Failed: {phrase!r}"

    def test_short_factual(self):
        assert classify_turn("what time is it") == TurnMode.SNAP
        assert classify_turn("what's the time") == TurnMode.SNAP
        assert classify_turn("what day is it") == TurnMode.SNAP
        assert classify_turn("how are you") == TurnMode.SNAP

    def test_continuations(self):
        for phrase in ["go ahead", "continue", "keep going", "go on"]:
            assert classify_turn(phrase) == TurnMode.SNAP, f"Failed: {phrase!r}"

    def test_short_unknown(self):
        """Short utterances without depth markers should still be SNAP."""
        assert classify_turn("red") == TurnMode.SNAP
        assert classify_turn("three o'clock") == TurnMode.SNAP
        assert classify_turn("maybe later") == TurnMode.SNAP

    def test_empty_and_whitespace(self):
        assert classify_turn("") == TurnMode.SNAP
        assert classify_turn("   ") == TurnMode.SNAP

    def test_that_is_all(self):
        assert classify_turn("that's all") == TurnMode.SNAP
        assert classify_turn("that is enough") == TurnMode.SNAP


class TestDeepClassification:
    """Utterances that should be classified as DEEP."""

    def test_code_requests(self):
        assert classify_turn("Can you implement a binary search function") == TurnMode.DEEP
        assert classify_turn("Debug this code for me please") == TurnMode.DEEP
        assert classify_turn("Write a function that sorts a list") == TurnMode.DEEP

    def test_explicit_depth_markers(self):
        assert classify_turn("Explain how neural networks work in detail") == TurnMode.DEEP
        assert classify_turn("Walk me through the process of photosynthesis") == TurnMode.DEEP
        assert classify_turn("Break down the architecture of a compiler") == TurnMode.DEEP

    def test_long_utterances(self):
        long_text = " ".join(["word"] * 45)  # 45 words
        assert classify_turn(long_text) == TurnMode.DEEP

    def test_reasoning(self):
        assert classify_turn("Compare and contrast REST and GraphQL APIs") == TurnMode.DEEP
        assert classify_turn("What are the pros and cons of using microservices") == TurnMode.DEEP

    def test_creative_building(self):
        assert classify_turn("Can you help me build a web application for tracking expenses") == TurnMode.DEEP
        assert classify_turn("Let's design a new authentication system") == TurnMode.DEEP

    def test_step_by_step(self):
        assert classify_turn("Can you give me a step by step guide to deploying on AWS") == TurnMode.DEEP


class TestLayeredClassification:
    """Utterances that should be classified as LAYERED (the default)."""

    def test_medium_questions(self):
        assert classify_turn("What do you think about climate change") == TurnMode.LAYERED
        assert classify_turn("Tell me about the history of Rome") == TurnMode.LAYERED

    def test_conversational(self):
        assert classify_turn("I've been thinking about switching careers") == TurnMode.LAYERED
        assert classify_turn("What would you recommend for dinner tonight") == TurnMode.LAYERED

    def test_moderate_length(self):
        assert classify_turn("I want to learn Python for data science next year") == TurnMode.LAYERED

    def test_not_too_short_not_deep(self):
        assert classify_turn("Tell me something interesting about space") == TurnMode.LAYERED
        assert classify_turn("What happened in the news today") == TurnMode.LAYERED


class TestPerformance:
    """Ensure classification is fast enough for the hot path."""

    def test_classification_under_1ms(self):
        """Each classification must complete in <1ms."""
        samples = [
            "hello",
            "What do you think about the economic impact of AI",
            "Explain how neural networks work in detail step by step",
            " ".join(["word"] * 50),
            "yes",
            "Write a function that implements quicksort",
            "Tell me about the weather",
        ]
        for text in samples:
            start = time.perf_counter_ns()
            classify_turn(text)
            elapsed_us = (time.perf_counter_ns() - start) / 1000
            assert elapsed_us < 1000, f"Too slow: {elapsed_us:.0f}µs for {text[:40]!r}"

    def test_batch_1000_under_100ms(self):
        """1000 classifications in <100ms total."""
        samples = ["hello", "explain quantum computing", "what time is it"] * 334
        start = time.perf_counter_ns()
        for text in samples:
            classify_turn(text)
        elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000
        assert elapsed_ms < 100, f"Batch too slow: {elapsed_ms:.1f}ms for 1000 calls"


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_case_insensitive(self):
        assert classify_turn("YES") == TurnMode.SNAP
        assert classify_turn("Hello") == TurnMode.SNAP
        assert classify_turn("THANKS") == TurnMode.SNAP

    def test_punctuation_stripped(self):
        assert classify_turn("yes.") == TurnMode.SNAP
        assert classify_turn("ok!") == TurnMode.SNAP
        assert classify_turn("thanks!") == TurnMode.SNAP

    def test_exactly_5_words_no_keywords(self):
        """At the SNAP boundary (5 words, no depth keywords)."""
        assert classify_turn("I like red blue green") == TurnMode.SNAP

    def test_exactly_6_words_no_keywords(self):
        """Just above the SNAP boundary without depth keywords → LAYERED."""
        assert classify_turn("I like red blue green purple") == TurnMode.LAYERED

    def test_deep_keyword_in_short_utterance(self):
        """Even short utterances with depth keywords → DEEP."""
        assert classify_turn("explain gravity") == TurnMode.DEEP
        assert classify_turn("debug my code") == TurnMode.DEEP


# ── v2 Three-Axis Tests ──────────────────────────────────────────────────────

class TestIntentClassification:
    """classify_turn_v2() correctly identifies intent."""

    def test_meta_self(self):
        r = classify_turn_v2("What do you think about when I'm not talking to you?")
        assert r.intent_class == IntentClass.META_SELF
        assert r.reasoning_mode == ReasoningMode.DELIBERATE_BURST

    def test_change_request(self):
        r = classify_turn_v2("Change your background to red")
        assert r.intent_class == IntentClass.CHANGE_REQUEST

    def test_mission_change(self):
        r = classify_turn_v2("Focus on science")
        assert r.intent_class == IntentClass.MISSION_CHANGE

    def test_self_edit(self):
        r = classify_turn_v2("Add a new command to your system")
        assert r.intent_class == IntentClass.SELF_EDIT
        assert r.reasoning_mode == ReasoningMode.DELIBERATE_BURST

    def test_emotional_probe(self):
        r = classify_turn_v2("How does that make you feel?")
        assert r.intent_class == IntentClass.EMOTIONAL_PROBE

    def test_repair(self):
        r = classify_turn_v2("That was wrong, fix that")
        assert r.intent_class == IntentClass.REPAIR

    def test_command_stop(self):
        r = classify_turn_v2("stop")
        assert r.intent_class == IntentClass.COMMAND
        assert r.reasoning_mode == ReasoningMode.REFLEX_ONLY

    def test_deep_factual_triggers_deliberation(self):
        long = "Can you explain " + "something very complex " * 5
        r = classify_turn_v2(long)
        assert r.depth_mode == TurnMode.DEEP

    def test_classification_is_dataclass(self):
        r = classify_turn_v2("hello")
        assert isinstance(r, TurnClassification)


class TestV2Performance:
    """Ensure v2 classification is still <1ms."""

    def test_v2_under_1ms(self):
        samples = [
            "What do you think about?",
            "Change background to red",
            "Focus on science",
            "Add a new command",
            "How does that make you feel?",
        ]
        for text in samples:
            start = time.perf_counter_ns()
            classify_turn_v2(text)
            elapsed_us = (time.perf_counter_ns() - start) / 1000
            assert elapsed_us < 1000, f"Too slow: {elapsed_us:.0f}µs for {text[:40]!r}"

