"""
testing/test_forge_workflow.py — Tests for the Forge self-edit workflow
"""
from apps.forged.planner import (
    EditPlan, EditVerdict, classify_file_tier, plan_edit,
    BUDGET_GREEN_MAX_FILES, BUDGET_GREEN_MAX_LINES,
)


class TestFileTierClassification:
    """Files are classified into correct edit tiers."""

    def test_immutable_forged(self):
        assert classify_file_tier("apps/forged/planner.py") == "immutable"

    def test_immutable_routerd(self):
        assert classify_file_tier("apps/routerd/serve.py") == "immutable"

    def test_immutable_guardian(self):
        assert classify_file_tier("spec/guardian_suite/runner.py") == "immutable"

    def test_immutable_docker(self):
        assert classify_file_tier("docker/Dockerfile") == "immutable"

    def test_critical_supervisor(self):
        assert classify_file_tier("pipeline/turn_supervisor.py") == "critical"

    def test_critical_vad(self):
        assert classify_file_tier("pipeline/vad.py") == "critical"

    def test_critical_invariants(self):
        assert classify_file_tier("spec/invariants.yaml") == "critical"

    def test_critical_edit_policies(self):
        assert classify_file_tier("spec/edit_policies.yml") == "critical"

    def test_mutable_mind(self):
        assert classify_file_tier("mind/scheduler.py") == "mutable"

    def test_mutable_static(self):
        assert classify_file_tier("static/index.html") == "mutable"

    def test_default_mutable(self):
        assert classify_file_tier("some/new/file.py") == "mutable"


class TestEditPlanning:
    """Forge planner produces correct verdicts."""

    def test_block_immutable(self):
        plan = plan_edit("test", ["apps/forged/planner.py"])
        assert plan.verdict == EditVerdict.BLOCKED
        assert "immutable" in plan.blocked_reason

    def test_critical_needs_approval(self):
        plan = plan_edit("test", ["pipeline/turn_supervisor.py"], estimated_lines=10)
        assert plan.verdict == EditVerdict.NEEDS_APPROVAL
        assert plan.risk_tier == "critical"
        assert plan.requires_restart

    def test_mutable_small_approved(self):
        plan = plan_edit(
            "Add logging",
            ["mind/scheduler.py"],
            estimated_lines=20,
        )
        assert plan.verdict == EditVerdict.APPROVED
        assert plan.can_proceed

    def test_over_budget(self):
        files = [f"mind/file_{i}.py" for i in range(10)]
        plan = plan_edit("big change", files, estimated_lines=600)
        assert plan.verdict == EditVerdict.OVER_BUDGET
        assert not plan.can_proceed

    def test_green_auto_threshold(self):
        plan = plan_edit("tiny fix", ["mind/scheduler.py"], estimated_lines=50)
        assert plan.verdict == EditVerdict.APPROVED

    def test_yellow_needs_approval(self):
        files = [f"mind/file_{i}.py" for i in range(5)]
        plan = plan_edit("medium change", files, estimated_lines=200)
        assert plan.verdict == EditVerdict.NEEDS_APPROVAL

    def test_affected_tests_found(self):
        plan = plan_edit("test", ["pipeline/turn_supervisor.py"])
        assert "testing/test_floor_sovereignty.py" in plan.affected_tests
        assert "testing/test_turn_control.py" in plan.affected_tests

    def test_affected_tests_for_mind(self):
        plan = plan_edit("test", ["mind/scheduler.py"])
        assert "testing/test_scheduler.py" in plan.affected_tests

    def test_mixed_tier_uses_highest(self):
        plan = plan_edit(
            "mixed",
            ["pipeline/turn_supervisor.py", "mind/scheduler.py"],
            estimated_lines=10,
        )
        assert plan.risk_tier == "critical"
