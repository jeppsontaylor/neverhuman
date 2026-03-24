"""
apps/forged/planner.py — Forge Self-Edit Planner

Plans self-edits by:
1. Querying edit policies for tier check
2. Checking patch budgets
3. Identifying affected files and tests
4. Producing a structured edit plan

This is the planning phase of the Forge workflow.
The actual edit, verify, and deploy steps are in editor.py, verifier.py, deployer.py.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

log = logging.getLogger("gary.forge.planner")


class EditVerdict(str, Enum):
    APPROVED       = "approved"         # can proceed
    NEEDS_APPROVAL = "needs_approval"   # human must approve
    BLOCKED        = "blocked"          # immutable tier — cannot edit
    OVER_BUDGET    = "over_budget"      # exceeds patch budget


@dataclass
class EditPlan:
    """A structured plan for a self-edit."""
    verdict: EditVerdict
    target_files: list[str] = field(default_factory=list)
    affected_tests: list[str] = field(default_factory=list)
    risk_tier: str = "mutable"
    estimated_lines: int = 0
    description: str = ""
    blocked_reason: Optional[str] = None
    requires_restart: bool = False
    requires_reload: bool = False

    @property
    def can_proceed(self) -> bool:
        return self.verdict in (EditVerdict.APPROVED, EditVerdict.NEEDS_APPROVAL)


# ── Immutable paths (from edit_policies.yml) ─────────────────────────────────

_IMMUTABLE_PREFIXES = [
    "apps/forged/",
    "apps/routerd/",
    "spec/guardian_suite/",
    "memory/spool.py",
    "docker/",
]

_CRITICAL_PREFIXES = [
    "pipeline/turn_supervisor.py",
    "pipeline/vad.py",
    "spec/invariants.yaml",
    "spec/module_contracts.yaml",
    "spec/edit_policies.yml",
    "core/prompts/identity/",
    "testing/guardian/",
    "testing/replay_harness.py",
]

_MUTABLE_PREFIXES = [
    "mind/",
    "initiative/",
    "pipeline/context_pack.py",
    "core/prompts/lanes/",
    "static/",
]

# ── Patch budgets ────────────────────────────────────────────────────────────

BUDGET_GREEN_MAX_FILES = 3
BUDGET_GREEN_MAX_LINES = 100
BUDGET_YELLOW_MAX_FILES = 8
BUDGET_YELLOW_MAX_LINES = 500


def classify_file_tier(filepath: str) -> str:
    """Classify a file into its edit tier."""
    for prefix in _IMMUTABLE_PREFIXES:
        if filepath.startswith(prefix):
            return "immutable"
    for prefix in _CRITICAL_PREFIXES:
        if filepath.startswith(prefix) or filepath == prefix.rstrip("/"):
            return "critical"
    for prefix in _MUTABLE_PREFIXES:
        if filepath.startswith(prefix):
            return "mutable"
    return "mutable"  # default to mutable for unlisted files


def plan_edit(
    description: str,
    target_files: list[str],
    estimated_lines: int = 0,
) -> EditPlan:
    """Plan a self-edit and return the verdict.

    Checks:
    1. Are any target files immutable?
    2. Are any target files critical (needs human approval)?
    3. Does the edit exceed patch budgets?
    4. Which tests need to run?
    """
    # Check for immutable files
    for f in target_files:
        if classify_file_tier(f) == "immutable":
            return EditPlan(
                verdict=EditVerdict.BLOCKED,
                target_files=target_files,
                risk_tier="immutable",
                description=description,
                blocked_reason=f"Cannot edit immutable file: {f}",
            )

    # Determine highest risk tier
    tiers = [classify_file_tier(f) for f in target_files]
    risk_tier = "critical" if "critical" in tiers else "mutable"

    # Check patch budget
    num_files = len(target_files)
    if num_files > BUDGET_YELLOW_MAX_FILES or estimated_lines > BUDGET_YELLOW_MAX_LINES:
        return EditPlan(
            verdict=EditVerdict.OVER_BUDGET,
            target_files=target_files,
            risk_tier=risk_tier,
            estimated_lines=estimated_lines,
            description=description,
            blocked_reason=f"Exceeds budget: {num_files} files, {estimated_lines} lines",
        )

    # Determine verdict based on risk tier
    if risk_tier == "critical":
        verdict = EditVerdict.NEEDS_APPROVAL
    elif num_files <= BUDGET_GREEN_MAX_FILES and estimated_lines <= BUDGET_GREEN_MAX_LINES:
        verdict = EditVerdict.APPROVED
    else:
        verdict = EditVerdict.NEEDS_APPROVAL

    # Determine affected tests (would be populated from module_contracts/test_map)
    affected_tests = _find_affected_tests(target_files)

    return EditPlan(
        verdict=verdict,
        target_files=target_files,
        affected_tests=affected_tests,
        risk_tier=risk_tier,
        estimated_lines=estimated_lines,
        description=description,
        requires_restart=risk_tier == "critical",
    )


def _find_affected_tests(target_files: list[str]) -> list[str]:
    """Find tests that cover the target files.

    In production, this would query the code_atlas test_map table.
    For now, uses simple prefix matching.
    """
    tests = set()

    for f in target_files:
        if f.startswith("pipeline/turn_supervisor"):
            tests.add("testing/test_floor_sovereignty.py")
            tests.add("testing/test_turn_control.py")
        elif f.startswith("pipeline/turn_classifier"):
            tests.add("testing/test_turn_classifier.py")
        elif f.startswith("mind/"):
            tests.add("testing/test_scheduler.py")
            tests.add("testing/test_validators.py")
        elif f.startswith("core/self_model"):
            tests.add("testing/test_self_pack.py")
        elif f.startswith("core/change_router"):
            tests.add("testing/test_change_router.py")

    return sorted(tests)
