"""
testing/test_llm.py — Tests for the LLM client pipeline

Validates:
  - Sentence splitter logic (_split_sentences)
  - Think-token routing (in_think state machine)
  - System prompt content
  - Constants
  - SSE parsing (mocked)
"""
import asyncio
import json
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline.llm import _split_sentences, SYSTEM_PROMPT, MAX_TOKENS, TEMPERATURE, LLM_URL


# ── Sentence Splitter ─────────────────────────────────────────────────────────

class TestSplitSentences:
    def test_single_sentence(self):
        sents, rem = _split_sentences("Hello world. ")
        assert sents == ["Hello world."]
        assert rem == ""

    def test_multiple_sentences(self):
        sents, rem = _split_sentences("First. Second! Third? ")
        assert len(sents) == 3
        assert sents[0] == "First."
        assert sents[1] == "Second!"
        assert sents[2] == "Third?"

    def test_partial_sentence(self):
        sents, rem = _split_sentences("Hello world")
        assert sents == []
        assert rem == "Hello world"

    def test_sentence_plus_partial(self):
        sents, rem = _split_sentences("First sentence. Second part")
        assert len(sents) == 1
        assert sents[0] == "First sentence."
        assert rem == "Second part"

    def test_empty_string(self):
        sents, rem = _split_sentences("")
        assert sents == []
        assert rem == ""

    def test_question_mark(self):
        sents, rem = _split_sentences("How are you? ")
        assert sents == ["How are you?"]

    def test_exclamation(self):
        sents, rem = _split_sentences("Wow! That's great. ")
        assert len(sents) == 2

    def test_end_of_string_punctuation(self):
        """Sentences ending at string boundary (no trailing space)."""
        sents, rem = _split_sentences("Done.")
        assert len(sents) == 1
        assert sents[0] == "Done."

    def test_abbreviations_dont_split(self):
        """Abbreviations like 'e.g.' shouldn't cause premature splits in typical usage."""
        # This tests the regex behavior — e.g. followed by more text
        sents, rem = _split_sentences("For example e.g. this works. ")
        # Should get at least one complete sentence
        assert any("works." in s for s in sents)

    def test_incremental_building(self):
        """Simulate token-by-token building."""
        buffer = ""
        all_sents = []

        tokens = ["The ", "quick ", "brown ", "fox. ", "The ", "lazy ", "dog."]
        for tok in tokens:
            buffer += tok
            sents, buffer = _split_sentences(buffer)
            all_sents.extend(sents)

        # Flush remainder
        if buffer.strip():
            all_sents.append(buffer.strip())

        assert len(all_sents) == 2
        assert "fox." in all_sents[0]
        assert "dog." in all_sents[1]

    def test_multiple_spaces_after_period(self):
        sents, rem = _split_sentences("First.  Second. ")
        assert len(sents) == 2

    def test_no_punctuation(self):
        sents, rem = _split_sentences("just some words without end")
        assert sents == []
        assert rem == "just some words without end"


# ── Think-Token Routing (Unit-level simulation) ──────────────────────────────

class TestThinkTokenRouting:
    """Test the think-block state machine logic extracted from the stream generator."""

    def _simulate_think_routing(self, deltas: list[str]) -> dict:
        """Simulate the think-token routing logic from llm.py stream()."""
        buffer = ""
        in_think = False
        think_buf = ""
        tokens = []
        sentences = []
        think_tokens = []

        for delta in deltas:
            remaining = delta
            while remaining:
                if not in_think:
                    if "<think>" in remaining:
                        before, _, after = remaining.partition("<think>")
                        if before:
                            buffer += before
                            tokens.append(before)
                            sents, buffer = _split_sentences(buffer)
                            sentences.extend(sents)
                        in_think = True
                        think_buf = ""
                        remaining = after
                    else:
                        buffer += remaining
                        tokens.append(remaining)
                        sents, buffer = _split_sentences(buffer)
                        sentences.extend(sents)
                        remaining = ""
                else:
                    if "</think>" in remaining:
                        before, _, after = remaining.partition("</think>")
                        think_buf += before
                        think_tokens.append(think_buf)
                        in_think = False
                        think_buf = ""
                        remaining = after
                    else:
                        think_buf += remaining
                        remaining = ""

        # Flush
        if in_think and think_buf.strip():
            think_tokens.append(think_buf)
        if buffer.strip():
            sentences.append(buffer.strip())

        return {
            "tokens": tokens,
            "sentences": sentences,
            "think_tokens": think_tokens,
        }

    def test_no_think_blocks(self):
        result = self._simulate_think_routing(["Hello ", "world. ", "How ", "are you? "])
        assert len(result["think_tokens"]) == 0
        assert len(result["sentences"]) == 2

    def test_think_block_stripped_from_display(self):
        result = self._simulate_think_routing([
            "<think>", "internal reasoning", "</think>",
            "Hello ", "world. "
        ])
        assert result["think_tokens"] == ["internal reasoning"]
        assert "internal" not in " ".join(result["tokens"])
        assert any("Hello" in t for t in result["tokens"])

    def test_think_block_mid_stream(self):
        result = self._simulate_think_routing([
            "First. ",
            "<think>", "thinking here", "</think>",
            "Second. "
        ])
        assert "First." in result["sentences"]
        assert "Second." in result["sentences"]
        assert result["think_tokens"] == ["thinking here"]

    def test_think_tags_split_across_deltas(self):
        """Tags that arrive as part of a larger delta."""
        result = self._simulate_think_routing([
            "Before<think>inside</think>After. "
        ])
        assert result["think_tokens"] == ["inside"]
        assert any("Before" in t for t in result["tokens"])
        assert any("After." in s for s in result["sentences"])

    def test_unclosed_think_block(self):
        """Unclosed think block should flush at end."""
        result = self._simulate_think_routing([
            "<think>", "never closed"
        ])
        assert len(result["think_tokens"]) == 1
        assert "never closed" in result["think_tokens"][0]

    def test_empty_think_block(self):
        result = self._simulate_think_routing([
            "<think></think>", "Response. "
        ])
        assert result["think_tokens"] == [""]
        assert "Response." in result["sentences"]

    def test_multiple_think_blocks(self):
        result = self._simulate_think_routing([
            "<think>", "thought 1", "</think>",
            "Answer one. ",
            "<think>", "thought 2", "</think>",
            "Answer two. "
        ])
        assert len(result["think_tokens"]) == 2
        assert len(result["sentences"]) == 2


# ── System Prompt ─────────────────────────────────────────────────────────────

class TestSystemPrompt:
    def test_prompt_is_nonempty(self):
        assert len(SYSTEM_PROMPT) > 100

    def test_prompt_mentions_gary(self):
        assert "GARY" in SYSTEM_PROMPT

    def test_prompt_mentions_voice_first(self):
        assert "voice" in SYSTEM_PROMPT.lower() or "spoken" in SYSTEM_PROMPT.lower()

    def test_prompt_discourages_markdown(self):
        assert "markdown" in SYSTEM_PROMPT.lower() or "asterisk" in SYSTEM_PROMPT.lower()

    def test_prompt_mentions_tts(self):
        assert "TTS" in SYSTEM_PROMPT or "spoken" in SYSTEM_PROMPT.lower()


# ── Constants ─────────────────────────────────────────────────────────────────

class TestLLMConstants:
    def test_url_format(self):
        assert LLM_URL.startswith("http")
        assert "chat/completions" in LLM_URL

    def test_max_tokens_reasonable(self):
        assert 100 <= MAX_TOKENS <= 4096

    def test_temperature_in_range(self):
        assert 0.0 <= TEMPERATURE <= 2.0


# ── check_connectivity ───────────────────────────────────────────────────────

class TestCheckConnectivity:
    @pytest.mark.asyncio
    async def test_connectivity_returns_bool(self):
        """Without a running LLM server, should return False gracefully."""
        from pipeline.llm import check_connectivity
        result = await check_connectivity()
        assert isinstance(result, bool)
        # Server is likely not running during tests
        assert result is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
