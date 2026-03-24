"""
testing/test_memory.py — Tests for the Memory Spine (v5.0)

Tests that don't require a live Postgres instance:
  - Schema SQL syntax validation (12 tables)
  - Event spool mechanics (local file I/O)
  - Event writer spool integration
  - Retrieval fusion scoring math (4 modes)
  - Context packing budget logic
  - Mode-specific weight validation
  - Retention tier support
  - Training buffer schema validation
"""
import asyncio
import json
import math
import os
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Retrieval Scoring Tests ────────────────────────────────────────────────────

class TestFusionScoring:
    """Test the fusion score computation (no DB required)."""

    def _get_retriever(self):
        from memory.retrieval import ContextRetriever
        return ContextRetriever()

    def test_recent_memory_scores_higher(self):
        retriever = self._get_retriever()
        now = time.time()

        recent = {
            "similarity": 0.8, "created_ts": now - 3600,
            "salience": 0.5, "source_event_id": "", "metadata": {},
        }
        old = {
            "similarity": 0.8, "created_ts": now - 7 * 86400,
            "salience": 0.5, "source_event_id": "", "metadata": {},
        }

        scores_recent = retriever._compute_scores(recent, now, set(), None)
        scores_old = retriever._compute_scores(old, now, set(), None)
        assert scores_recent["recency"] > scores_old["recency"]

    def test_salient_memory_scores_higher(self):
        retriever = self._get_retriever()
        now = time.time()

        high_sal = {
            "similarity": 0.7, "created_ts": now - 3600,
            "salience": 0.95, "source_event_id": "", "metadata": {},
        }
        low_sal = {
            "similarity": 0.7, "created_ts": now - 3600,
            "salience": 0.1, "source_event_id": "", "metadata": {},
        }

        scores_high = retriever._compute_scores(high_sal, now, set(), None)
        scores_low = retriever._compute_scores(low_sal, now, set(), None)
        assert scores_high["salience"] > scores_low["salience"]

    def test_open_loop_boost(self):
        retriever = self._get_retriever()
        now = time.time()

        candidate = {
            "similarity": 0.5, "created_ts": now,
            "salience": 0.5, "source_event_id": "loop-123", "metadata": {},
        }

        scores_with = retriever._compute_scores(candidate, now, {"loop-123"}, None)
        scores_without = retriever._compute_scores(candidate, now, set(), None)
        assert scores_with["open_loop"] > 0
        assert scores_without["open_loop"] == 0

    def test_affect_congruence(self):
        retriever = self._get_retriever()
        now = time.time()
        affect = {"loneliness": 0.8, "anxiety": 0.5, "excitement": 0.1}

        congruent = {
            "similarity": 0.5, "created_ts": now, "salience": 0.5,
            "source_event_id": "",
            "metadata": json.dumps({
                "affect_at_creation": {"loneliness": 0.7, "anxiety": 0.6, "excitement": 0.2}
            }),
        }
        incongruent = {
            "similarity": 0.5, "created_ts": now, "salience": 0.5,
            "source_event_id": "",
            "metadata": json.dumps({
                "affect_at_creation": {"loneliness": 0.0, "anxiety": 0.0, "excitement": 0.9}
            }),
        }

        # Use relational mode (has affect weight > 0)
        from memory.retrieval import WEIGHT_PROFILES
        relational_weights = WEIGHT_PROFILES["relational"]
        scores_c = retriever._compute_scores(congruent, now, set(), affect, relational_weights)
        scores_i = retriever._compute_scores(incongruent, now, set(), affect, relational_weights)
        assert scores_c["affect"] > scores_i["affect"]

    def test_recency_decay_formula(self):
        """After one half-life, decay should be ~0.5."""
        from memory.retrieval import RECENCY_HALF_LIFE_SEC, WEIGHT_PROFILES
        now = time.time()
        candidate = {
            "similarity": 0.5, "created_ts": now - RECENCY_HALF_LIFE_SEC,
            "salience": 0.5, "source_event_id": "", "metadata": {},
        }
        retriever = self._get_retriever()
        weights = WEIGHT_PROFILES["factual"]
        scores = retriever._compute_scores(candidate, now, set(), None, weights)
        expected_recency = weights["recency"] * 0.5
        assert abs(scores["recency"] - expected_recency) < 0.01


# ── Mode-Specific Weight Tests ────────────────────────────────────────────────

class TestModeSpecificWeights:
    """Test that all 4 retrieval modes have valid weight profiles."""

    def test_all_modes_sum_to_one(self):
        from memory.retrieval import WEIGHT_PROFILES
        for mode, weights in WEIGHT_PROFILES.items():
            total = sum(weights.values())
            assert abs(total - 1.0) < 0.001, f"Mode {mode} weights sum to {total}, not 1.0"

    def test_factual_mode_no_affect(self):
        """Factual queries should not use affect matching."""
        from memory.retrieval import WEIGHT_PROFILES
        assert WEIGHT_PROFILES["factual"]["affect"] == 0.0

    def test_relational_mode_heavy_open_loop(self):
        """Relational mode should prioritize open loops."""
        from memory.retrieval import WEIGHT_PROFILES
        assert WEIGHT_PROFILES["relational"]["open_loop"] >= 0.25

    def test_dreaming_mode_heavy_affect(self):
        """Dreaming mode should heavily weight affect congruence."""
        from memory.retrieval import WEIGHT_PROFILES
        assert WEIGHT_PROFILES["dreaming"]["affect"] >= 0.25

    def test_all_modes_exist(self):
        from memory.retrieval import WEIGHT_PROFILES
        expected = {"factual", "relational", "reflection", "dreaming"}
        assert set(WEIGHT_PROFILES.keys()) == expected

    def test_factual_highest_semantic(self):
        """Factual mode should have the highest semantic weight."""
        from memory.retrieval import WEIGHT_PROFILES
        factual_sem = WEIGHT_PROFILES["factual"]["semantic"]
        for mode, weights in WEIGHT_PROFILES.items():
            if mode != "factual":
                assert factual_sem >= weights["semantic"], \
                    f"Factual semantic ({factual_sem}) should be >= {mode} ({weights['semantic']})"

    def test_mode_specific_scoring_changes_results(self):
        """Different modes should produce different scores for the same candidate."""
        from memory.retrieval import ContextRetriever, WEIGHT_PROFILES
        retriever = ContextRetriever()
        now = time.time()

        candidate = {
            "similarity": 0.7, "created_ts": now - 3600,
            "salience": 0.5, "source_event_id": "loop-1",
            "metadata": json.dumps({
                "affect_at_creation": {"loneliness": 0.8, "excitement": 0.3}
            }),
        }
        affect = {"loneliness": 0.7, "excitement": 0.4}

        scores = {}
        for mode, weights in WEIGHT_PROFILES.items():
            s = retriever._compute_scores(candidate, now, {"loop-1"}, affect, weights)
            scores[mode] = sum(s.values())

        # Different modes should produce different total scores
        unique_scores = set(round(v, 4) for v in scores.values())
        assert len(unique_scores) > 1, "All modes produced identical scores"


# ── Event Spool Tests ─────────────────────────────────────────────────────────

class TestEventSpool:
    """Test the local append-only event spool."""

    def test_spool_creates_directory(self):
        from memory.spool import EventSpool
        with tempfile.TemporaryDirectory() as tmpdir:
            spool_dir = os.path.join(tmpdir, "new_spool")
            spool = EventSpool(spool_dir=spool_dir)
            assert os.path.isdir(spool_dir)

    def test_append_creates_file(self):
        from memory.spool import EventSpool
        with tempfile.TemporaryDirectory() as tmpdir:
            spool = EventSpool(spool_dir=tmpdir)
            spool.append({"kind": "test", "payload": {"msg": "hello"}})
            spool_file = os.path.join(tmpdir, "active.jsonl")
            assert os.path.exists(spool_file)

    def test_append_writes_valid_jsonl(self):
        from memory.spool import EventSpool
        with tempfile.TemporaryDirectory() as tmpdir:
            spool = EventSpool(spool_dir=tmpdir)
            spool.append({"kind": "utterance", "text": "hello"})
            spool.append({"kind": "response", "text": "world"})

            spool_file = os.path.join(tmpdir, "active.jsonl")
            with open(spool_file) as f:
                lines = [l.strip() for l in f if l.strip()]

            assert len(lines) == 2
            for line in lines:
                data = json.loads(line)  # should not raise
                assert "kind" in data

    def test_append_with_pydantic_model(self):
        from memory.spool import EventSpool
        from core.events import UtteranceEvent
        with tempfile.TemporaryDirectory() as tmpdir:
            spool = EventSpool(spool_dir=tmpdir)
            event = UtteranceEvent.from_transcript("s1", "hello world", 0.95, 150)
            ok = spool.append(event)
            assert ok is True
            assert spool.stats["appended"] == 1

    def test_pending_count(self):
        from memory.spool import EventSpool
        with tempfile.TemporaryDirectory() as tmpdir:
            spool = EventSpool(spool_dir=tmpdir)
            assert spool.pending_count == 0
            spool.append({"kind": "test1"})
            spool.append({"kind": "test2"})
            spool.append({"kind": "test3"})
            assert spool.pending_count == 3

    def test_stats_tracking(self):
        from memory.spool import EventSpool
        with tempfile.TemporaryDirectory() as tmpdir:
            spool = EventSpool(spool_dir=tmpdir)
            spool.append({"a": 1})
            spool.append({"b": 2})
            assert spool.stats["appended"] == 2
            assert spool.stats["errors"] == 0


# ── Event Writer Spool Integration ────────────────────────────────────────────

class TestEventWriterSpool:
    """Test that EventWriter correctly delegates to EventSpool."""

    def test_emit_writes_to_spool(self):
        from memory.event_writer import EventWriter
        from core.events import UtteranceEvent
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = EventWriter(spool_dir=tmpdir)
            event = UtteranceEvent.from_transcript("s1", "hello", 1.0, 100)
            ok = writer.emit(event)
            assert ok is True
            assert writer.stats["emitted"] == 1

    def test_emit_multiple_events(self):
        from memory.event_writer import EventWriter
        from core.events import UtteranceEvent
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = EventWriter(spool_dir=tmpdir)
            for i in range(5):
                event = UtteranceEvent.from_transcript("s1", f"msg {i}", 1.0, 100)
                writer.emit(event)
            assert writer.stats["emitted"] == 5
            assert writer.pending_count == 5

    def test_spool_file_has_correct_content(self):
        from memory.event_writer import EventWriter
        from core.events import ResponseEvent
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = EventWriter(spool_dir=tmpdir)
            event = ResponseEvent.from_response("s1", "I can help with that", 200)
            writer.emit(event)

            spool_file = os.path.join(tmpdir, "active.jsonl")
            with open(spool_file) as f:
                lines = [l.strip() for l in f if l.strip()]
            assert len(lines) == 1
            data = json.loads(lines[0])
            assert data["kind"] == "response"


# ── Schema v5 SQL Syntax Tests ────────────────────────────────────────────────

class TestSchemaSyntaxV5:
    """Validate the v5 schema file (12 tables)."""

    def _read_schema(self):
        schema_path = os.path.join(os.path.dirname(__file__), "..", "memory", "schema.sql")
        with open(schema_path) as f:
            return f.read()

    def test_schema_file_exists(self):
        schema_path = os.path.join(os.path.dirname(__file__), "..", "memory", "schema.sql")
        assert os.path.exists(schema_path)

    def test_schema_has_all_12_tables(self):
        sql = self._read_schema()
        expected_tables = [
            "events", "claims", "memories", "thoughts", "affect_state",
            "open_loops", "ideas", "rewards", "initiative_logs",
            "artifacts", "voice_profiles", "training_buffer",
        ]
        for table in expected_tables:
            assert f"CREATE TABLE {table}" in sql, f"Missing table: {table}"

    def test_schema_has_training_runs(self):
        sql = self._read_schema()
        assert "CREATE TABLE training_runs" in sql

    def test_schema_has_claims_contradiction_edges(self):
        sql = self._read_schema()
        assert "superseded_by" in sql
        assert "contradicted_by" in sql

    def test_schema_has_causality_fields(self):
        sql = self._read_schema()
        assert "session_seq" in sql
        assert "turn_id" in sql
        assert "parent_id" in sql

    def test_schema_has_retention_tiers(self):
        sql = self._read_schema()
        assert "retention_tier" in sql
        assert "expires_at" in sql

    def test_schema_has_training_buffer_safety_gates(self):
        sql = self._read_schema()
        assert "speaker_conf" in sql
        assert "consent_ok" in sql
        assert "no_tts_bleed" in sql
        assert "holdout_bucket" in sql

    def test_schema_has_granular_consent(self):
        sql = self._read_schema()
        consent_scopes = [
            "consent_save_audio", "consent_save_transcript",
            "consent_asr_training", "consent_voice_cloning",
            "consent_long_term_memory", "consent_export",
            "consent_training_sets",
        ]
        for scope in consent_scopes:
            assert scope in sql, f"Missing consent scope: {scope}"

    def test_schema_has_appraisal_field(self):
        sql = self._read_schema()
        assert "appraisal" in sql

    def test_schema_has_truth_gated_rewards(self):
        sql = self._read_schema()
        assert "truth_gated" in sql

    def test_schema_has_evidence_score(self):
        sql = self._read_schema()
        assert "evidence_score" in sql

    def test_schema_has_listen_notify(self):
        sql = self._read_schema()
        assert "pg_notify" in sql
        assert "gary_events" in sql

    def test_schema_has_pgvector(self):
        sql = self._read_schema()
        assert "CREATE EXTENSION IF NOT EXISTS vector" in sql
        assert "vector(384)" in sql

    def test_schema_has_partitioning(self):
        sql = self._read_schema()
        assert "PARTITION BY RANGE" in sql
        assert "events_default" in sql

    def test_schema_has_training_eligible_view(self):
        sql = self._read_schema()
        assert "v_training_eligible" in sql

    def test_schema_has_active_claims_view(self):
        sql = self._read_schema()
        assert "v_active_claims" in sql

    def test_schema_has_initiative_shadow_mode(self):
        sql = self._read_schema()
        assert "shadow" in sql
        assert "draft_text" in sql
        assert "score_breakdown" in sql


# ── Docker Compose Validation ─────────────────────────────────────────────────

class TestDockerCompose:
    def test_compose_file_exists(self):
        compose_path = os.path.join(os.path.dirname(__file__), "..", "docker", "compose.yml")
        assert os.path.exists(compose_path)

    def test_compose_has_pgvector_image(self):
        compose_path = os.path.join(os.path.dirname(__file__), "..", "docker", "compose.yml")
        with open(compose_path) as f:
            content = f.read()
        assert "pgvector/pgvector:pg16" in content
        assert "pgdata" in content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
