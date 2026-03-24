"""
testing/test_eval_metrics.py — Tests for eval metrics collector
"""
from core.eval_metrics import EvalMetrics, MetricCounter


class TestMetricCounter:
    """Basic counter for success/failure rates."""

    def test_empty_counter(self):
        mc = MetricCounter()
        assert mc.total == 0
        assert mc.rate == 1.0  # default when no data

    def test_all_success(self):
        mc = MetricCounter()
        mc.record_success()
        mc.record_success()
        assert mc.total == 2
        assert mc.rate == 1.0
        assert mc.failure_rate == 0.0

    def test_mixed(self):
        mc = MetricCounter()
        mc.record_success()
        mc.record_failure()
        assert mc.rate == 0.5
        assert mc.failure_rate == 0.5


class TestEvalMetrics:
    """System-wide eval metrics."""

    def test_record_turn(self):
        metrics = EvalMetrics()
        metrics.record_turn(floor_violation=False, orphaned=False)
        metrics.record_turn(floor_violation=True, orphaned=False)
        report = metrics.report()
        assert report["floor_violation_rate"] == 50.0
        assert report["total_turns"] == 2

    def test_no_violations_healthy(self):
        metrics = EvalMetrics()
        for _ in range(10):
            metrics.record_turn()
            metrics.record_initiative_attempt(during_debt=False)
            metrics.record_pulse_quality()
        healthy, violations = metrics.check_health()
        assert healthy
        assert len(violations) == 0

    def test_violations_unhealthy(self):
        metrics = EvalMetrics()
        metrics.record_turn(floor_violation=True)
        healthy, violations = metrics.check_health()
        assert not healthy
        assert len(violations) > 0

    def test_psychologizing_threshold(self):
        metrics = EvalMetrics()
        # 12 pulses: 1 psychologizing (8.3%) > 5% threshold
        for _ in range(11):
            metrics.record_pulse_quality()
        metrics.record_pulse_quality(psychologizing=True)
        healthy, violations = metrics.check_health()
        assert not healthy
        assert any("Psychologizing" in v for v in violations)

    def test_quest_continuity(self):
        metrics = EvalMetrics()
        metrics.record_quest_continuity(0.8)
        metrics.record_quest_continuity(0.6)
        report = metrics.report()
        assert report["quest_continuity_avg"] == 0.7

    def test_self_edit_tracking(self):
        metrics = EvalMetrics()
        metrics.record_self_edit(passed=True)
        metrics.record_self_edit(passed=True)
        metrics.record_self_edit(passed=False)
        report = metrics.report()
        assert abs(report["self_edit_pass_rate"] - 66.67) < 0.01

    def test_report_format(self):
        metrics = EvalMetrics()
        report = metrics.report()
        expected_keys = [
            "uptime_sec", "floor_violation_rate", "initiative_during_debt_rate",
            "orphaned_turn_rate", "psychologizing_rate", "scratchpad_leak_rate",
            "work_product_yield", "quest_continuity_avg", "self_edit_pass_rate",
            "rollback_success_rate", "kept_changes_24h", "total_turns",
        ]
        for key in expected_keys:
            assert key in report, f"Missing key: {key}"
