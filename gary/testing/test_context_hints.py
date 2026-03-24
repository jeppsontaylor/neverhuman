"""
testing/test_context_hints.py — Tests for ASR context hint injection

Validates:
  - Static hints loaded
  - User terms management
  - Session term extraction from text
  - Context string generation (dedup, ordering)
  - Edge cases: empty, caps, hyphens
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline.context_hints import ContextHints


class TestContextHintsInit:
    def test_has_static_hints(self):
        h = ContextHints()
        counts = h.get_counts()
        assert counts["static"] > 10

    def test_context_string_nonempty(self):
        h = ContextHints()
        ctx = h.get_context_string()
        assert len(ctx) > 0
        assert "GARY" in ctx


class TestUserTerms:
    def test_add_user_terms(self):
        h = ContextHints()
        h.add_user_terms(["EigenIntel", "VEOX", "MetaPulse"])
        counts = h.get_counts()
        assert counts["user"] == 3

    def test_user_terms_in_context(self):
        h = ContextHints()
        h.add_user_terms(["CustomTerm123"])
        ctx = h.get_context_string()
        assert "CustomTerm123" in ctx

    def test_user_terms_deduplicate(self):
        h = ContextHints()
        h.add_user_terms(["Alpha", "Alpha", "Beta"])
        assert h.get_counts()["user"] == 2


class TestSessionTerms:
    def test_add_session_term(self):
        h = ContextHints()
        h.add_session_term("SuperWidget")
        assert "SuperWidget" in h.get_context_string()

    def test_extract_proper_nouns(self):
        h = ContextHints()
        h.add_session_terms_from_text("I need to talk to Benjamin about the MacBook Pro.")
        ctx = h.get_context_string()
        # Benjamin and MacBook should be extracted (capitalized, not at sentence start)
        assert "Benjamin" in ctx
        assert "MacBook" in ctx

    def test_extract_acronyms(self):
        h = ContextHints()
        h.add_session_terms_from_text("We should use REST API and check the GPU status")
        ctx = h.get_context_string()
        assert "REST" in ctx or "API" in ctx or "GPU" in ctx

    def test_extract_hyphenated(self):
        h = ContextHints()
        h.add_session_terms_from_text("The flash-moe engine works great")
        ctx = h.get_context_string()
        assert "flash-moe" in ctx

    def test_clear_session(self):
        h = ContextHints()
        h.add_session_term("TempTerm")
        h.clear_session()
        assert h.get_counts()["session"] == 0


class TestContextString:
    def test_deduplication(self):
        h = ContextHints()
        # "GARY" is already in static hints
        h.add_user_terms(["GARY"])
        ctx = h.get_context_string()
        # Should appear only once
        parts = [p.strip() for p in ctx.split(",")]
        gary_count = sum(1 for p in parts if p.lower() == "gary")
        assert gary_count == 1

    def test_session_terms_first(self):
        h = ContextHints()
        h.add_session_term("VerySpecificTerm")
        ctx = h.get_context_string()
        # Session terms should come before static
        idx_session = ctx.index("VerySpecificTerm")
        idx_gary = ctx.index("GARY")
        assert idx_session < idx_gary

    def test_max_hints_cap(self):
        h = ContextHints()
        h._max_hints = 5
        h.add_user_terms([f"term_{i}" for i in range(100)])
        ctx = h.get_context_string()
        parts = [p.strip() for p in ctx.split(",")]
        assert len(parts) <= 5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
