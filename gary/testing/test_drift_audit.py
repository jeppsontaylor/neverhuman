"""
testing/test_drift_audit.py — Tests for docs-runtime drift audit
"""
from core.drift_audit import (
    DriftEvent, DriftReport, audit_module_exports, audit_file_exists,
    audit_self_model_freshness, run_drift_audit,
)
from core.self_model import compile_self_pack
import time


class TestDriftEvent:
    """DriftEvent data model."""

    def test_event_str(self):
        e = DriftEvent(
            category="contract",
            module="pipeline.turn_supervisor",
            expected="export 'FakeClass'",
            actual="not found",
            severity="error",
        )
        assert "contract" in str(e)
        assert "FakeClass" in str(e)


class TestDriftReport:
    """DriftReport aggregation."""

    def test_empty_is_clean(self):
        r = DriftReport()
        assert r.clean
        assert "No drift" in r.summary()

    def test_with_events(self):
        r = DriftReport()
        r.events.append(DriftEvent("test", "mod", "x", "y", "warning"))
        assert not r.clean
        assert r.error_count == 0
        r.events.append(DriftEvent("test", "mod", "x", "y", "error"))
        assert r.error_count == 1


class TestModuleExportAudit:
    """Check that modules export what contracts say."""

    def test_valid_exports(self):
        events = audit_module_exports(
            "pipeline.turn_classifier",
            ["TurnMode", "classify_turn"]
        )
        assert len(events) == 0

    def test_missing_export(self):
        events = audit_module_exports(
            "pipeline.turn_classifier",
            ["TurnMode", "NonexistentFakeClass"]
        )
        assert len(events) == 1
        assert events[0].severity == "error"

    def test_bad_module(self):
        events = audit_module_exports(
            "does.not.exist.module",
            ["Foo"]
        )
        assert len(events) == 1
        assert "ImportError" in events[0].actual


class TestFileExistenceAudit:
    """Check that critical files exist."""

    def test_existing_files(self):
        events = audit_file_exists([
            "pipeline/turn_supervisor.py",
            "pipeline/turn_classifier.py",
        ])
        assert len(events) == 0

    def test_missing_file(self):
        events = audit_file_exists(["totally_fake_file.py"])
        assert len(events) == 1


class TestSelfModelFreshnessAudit:
    """Check self-model staleness."""

    def test_fresh_pack(self):
        pack = compile_self_pack(start_time=time.monotonic())
        events = audit_self_model_freshness(pack)
        assert len(events) == 0  # all fresh

    def test_none_pack(self):
        events = audit_self_model_freshness(None)
        assert len(events) == 1
        assert events[0].severity == "error"


class TestFullDriftAudit:
    """Full drift audit integration."""

    def test_full_audit_clean(self):
        pack = compile_self_pack(start_time=time.monotonic())
        report = run_drift_audit(self_pack=pack)
        # Should find no errors (all modules exist and export correctly)
        assert report.error_count == 0, report.summary()

    def test_full_audit_without_pack(self):
        report = run_drift_audit()
        # Runs without error, no self-model check
        assert isinstance(report, DriftReport)
