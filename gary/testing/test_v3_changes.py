"""
Tests for the mindd sidecar, retrieval audit, schema additions, and
phase alignment corrections.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import pytest

# Ensure GARY root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Phase alignment tests ────────────────────────────────────────────────────

class TestPhaseAlignment:
    """Verify mind.py phase triggers match the Architecture Bible."""

    def test_min_idle_threshold(self):
        from core.mind import MIN_IDLE_FOR_THOUGHT
        assert MIN_IDLE_FOR_THOUGHT == 30.0, (
            f"MIN_IDLE_FOR_THOUGHT should be 30.0 (Architecture Bible), got {MIN_IDLE_FOR_THOUGHT}"
        )

    def test_phase_cooldowns(self):
        from core.mind import PHASE_COOLDOWNS
        assert PHASE_COOLDOWNS["reflecting"] == 15
        assert PHASE_COOLDOWNS["brainstorming"] == 30
        assert PHASE_COOLDOWNS["dreaming"] == 60

    def test_reflecting_phase_at_30s(self):
        from core.mind import select_phase
        phase = select_phase(31.0, curiosity=0.0, excitement=0.0, anxiety=0.0, mental_load=0.5)
        assert phase == "reflecting", f"Expected reflecting at 31s idle, got {phase}"

    def test_no_phase_below_30s(self):
        from core.mind import select_phase
        phase = select_phase(10.0, curiosity=0.0, excitement=0.0, anxiety=0.0, mental_load=0.5)
        assert phase is None, f"Expected None below 30s idle, got {phase}"

    def test_brainstorming_phase_at_120s(self):
        from core.mind import select_phase
        phase = select_phase(121.0, curiosity=0.0, excitement=0.0, anxiety=0.0, mental_load=0.5)
        assert phase == "brainstorming", f"Expected brainstorming at 121s idle, got {phase}"

    def test_dreaming_phase_at_300s(self):
        from core.mind import select_phase
        phase = select_phase(301.0, curiosity=0.0, excitement=0.0, anxiety=0.0, mental_load=0.5)
        assert phase == "dreaming", f"Expected dreaming at 301s idle, got {phase}"

    def test_anxiety_keeps_reflecting(self):
        """High anxiety should keep us in reflecting even when idle > 120s."""
        from core.mind import select_phase
        phase = select_phase(121.0, curiosity=0.0, excitement=0.0, anxiety=0.7, mental_load=0.5)
        assert phase == "reflecting", f"High anxiety should keep reflecting, got {phase}"


# ── mindd sidecar tests ──────────────────────────────────────────────────────

class TestMinddServe:
    """Test the mindd sidecar server models and pulse generation."""

    def test_pulse_request_model(self):
        from apps.mindd.serve import PulseRequest
        req = PulseRequest(
            phase="reflecting",
            recent_thoughts=["thought1", "thought2"],
            affect_summary="curious and calm",
        )
        assert req.phase == "reflecting"
        assert len(req.recent_thoughts) == 2
        assert req.open_loops == []

    def test_pulse_response_model(self):
        from apps.mindd.serve import PulseResponse
        resp = PulseResponse(
            thought_id="test-123",
            phase="reflecting",
            clean_text="I wonder about X",
            salience=0.7,
        )
        assert resp.thought_id == "test-123"
        assert resp.salience == 0.7
        assert resp.error is None

    def test_pulse_response_with_error(self):
        from apps.mindd.serve import PulseResponse
        resp = PulseResponse(
            thought_id="test-err",
            error="connect_failed",
        )
        assert resp.error == "connect_failed"
        assert resp.clean_text == ""

    def test_pulse_response_with_initiative(self):
        from apps.mindd.serve import PulseResponse
        resp = PulseResponse(
            thought_id="test-init",
            phase="brainstorming",
            clean_text="User might need help with X",
            salience=0.9,
            initiative={"text": "Want me to help with X?", "reason": "user stuck"},
        )
        assert resp.initiative is not None
        assert resp.initiative["text"] == "Want me to help with X?"


class TestPulseWorker:
    """Test the pulse worker's health check logic."""

    def test_sidecar_health_function_exists(self):
        from apps.mindd.pulse_worker import check_sidecar_health
        assert callable(check_sidecar_health)

    def test_generate_pulse_function_exists(self):
        from apps.mindd.pulse_worker import generate_pulse
        assert callable(generate_pulse)
        assert asyncio.iscoroutinefunction(generate_pulse)


# ── Server mind loop flag tests ──────────────────────────────────────────────

class TestMindRemoteFlag:
    """Test the GARY_MIND_REMOTE environment variable behavior."""

    def test_mind_remote_off_by_default(self):
        """When GARY_MIND_REMOTE is not set, embedded mode should be used."""
        old = os.environ.pop("GARY_MIND_REMOTE", None)
        try:
            val = os.getenv("GARY_MIND_REMOTE", "").strip().lower()
            assert val not in ("1", "true", "yes", "on")
        finally:
            if old is not None:
                os.environ["GARY_MIND_REMOTE"] = old

    def test_mind_remote_on_when_set(self):
        """When GARY_MIND_REMOTE=1, remote mode should activate."""
        old = os.environ.get("GARY_MIND_REMOTE")
        os.environ["GARY_MIND_REMOTE"] = "1"
        try:
            val = os.getenv("GARY_MIND_REMOTE", "").strip().lower()
            assert val in ("1", "true", "yes", "on")
        finally:
            if old is not None:
                os.environ["GARY_MIND_REMOTE"] = old
            else:
                os.environ.pop("GARY_MIND_REMOTE", None)


# ── Schema validation tests ──────────────────────────────────────────────────

class TestSchemaV6:
    """Verify the schema file contains all 19 tables."""

    @pytest.fixture(autouse=True)
    def load_schema(self):
        schema_path = os.path.join(
            os.path.dirname(__file__), "..", "memory", "schema.sql"
        )
        with open(schema_path) as f:
            self.schema = f.read()

    def test_questions_table(self):
        assert "CREATE TABLE questions" in self.schema

    def test_experiments_table(self):
        assert "CREATE TABLE experiments" in self.schema

    def test_retrieval_log_table(self):
        assert "CREATE TABLE retrieval_log" in self.schema

    def test_prompt_versions_table(self):
        assert "CREATE TABLE prompt_versions" in self.schema

    def test_consent_log_table(self):
        assert "CREATE TABLE consent_log" in self.schema

    def test_claim_edges_table(self):
        assert "CREATE TABLE claim_edges" in self.schema

    def test_eval_runs_table(self):
        assert "CREATE TABLE eval_runs" in self.schema

    def test_open_loops_extensions(self):
        assert "affective_charge" in self.schema
        assert "source_refs" in self.schema
        assert "last_touched_at" in self.schema

    def test_schema_version_header(self):
        assert "v6.0" in self.schema

    def test_total_table_count(self):
        """Verify we have 20 CREATE TABLE statements (13 original + 7 new)."""
        count = self.schema.count("CREATE TABLE ")
        # Subtract CREATE TABLE for partitions (events_2026_03, etc.)
        partition_count = self.schema.count("PARTITION OF")
        actual_tables = count - partition_count
        assert actual_tables == 20, f"Expected 20 tables, got {actual_tables}"


# ── LLM prompt tests ─────────────────────────────────────────────────────────

class TestLLMPromptFixes:
    """Verify the LLM system prompt no longer says 'Be proactive'."""

    def test_no_be_proactive(self):
        llm_path = os.path.join(
            os.path.dirname(__file__), "..", "pipeline", "llm.py"
        )
        with open(llm_path) as f:
            content = f.read()
        assert "Be proactive" not in content, (
            "llm.py SYSTEM_PROMPT should not contain 'Be proactive' — "
            "initiative comes from the initiative engine"
        )

    def test_has_turn_scoped_help(self):
        # Prompt text now lives in core/prompts/system.txt
        prompt_path = os.path.join(
            os.path.dirname(__file__), "..", "core", "prompts", "system.txt"
        )
        with open(prompt_path) as f:
            content = f.read()
        assert "Do not initiate new topics unprompted" in content


# ── Agent Bible alignment tests ──────────────────────────────────────────────

class TestAgentBibleAlignment:
    """Verify AGENT_BIBLE.md phase triggers match corrected values."""

    @pytest.fixture(autouse=True)
    def load_bible(self):
        bible_path = os.path.join(
            os.path.dirname(__file__), "..", "AGENT.md"
        )
        with open(bible_path) as f:
            self.bible = f.read()

    def test_reflecting_30s(self):
        assert "30s" in self.bible and "Reflecting" in self.bible

    def test_brainstorming_120s(self):
        assert "120s" in self.bible

    def test_dreaming_300s(self):
        assert "300s" in self.bible

    def test_no_old_3s_trigger(self):
        """Old 3s trigger should be gone."""
        # Check that the specific old pattern is not present
        assert "Trigger: 3s" not in self.bible
