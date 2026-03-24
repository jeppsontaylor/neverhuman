"""
core/eval_metrics.py — Eval metrics collector for system health tracking

Tracks the key metrics from Phase 8B:
  - Floor violation rate (target: 0%)
  - Initiative during reply debt (target: 0%)
  - Orphaned turn rate (target: 0%)
  - Self-model accuracy (target: >90%)
  - Psychologizing rate (target: <5%)
  - Scratchpad leak rate (target: 0%)
  - Work-product yield (target: 100%)
  - Quest continuity (target: >0.5)
  - Self-edit pass rate (target: >95%)
  - Rollback success (target: 100%)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger("gary.eval_metrics")


@dataclass
class MetricCounter:
    """A simple success/failure counter for rate metrics."""
    successes: int = 0
    failures: int = 0

    def record_success(self) -> None:
        self.successes += 1

    def record_failure(self) -> None:
        self.failures += 1

    @property
    def total(self) -> int:
        return self.successes + self.failures

    @property
    def rate(self) -> float:
        """Success rate as fraction 0-1."""
        if self.total == 0:
            return 1.0
        return self.successes / self.total

    @property
    def failure_rate(self) -> float:
        """Failure rate as fraction 0-1."""
        return 1.0 - self.rate


class EvalMetrics:
    """System-wide eval metrics collector."""

    def __init__(self):
        self.floor_violations = MetricCounter()
        self.initiative_during_debt = MetricCounter()
        self.orphaned_turns = MetricCounter()
        self.self_model_accuracy = MetricCounter()
        self.psychologizing = MetricCounter()
        self.scratchpad_leaks = MetricCounter()
        self.work_product_yield = MetricCounter()
        self.quest_continuity_scores: list[float] = []
        self.self_edit_results = MetricCounter()
        self.rollback_results = MetricCounter()
        self.kept_change_count: int = 0
        self._started_at: float = time.monotonic()

    # ── Recording methods ────────────────────────────────────────────────────

    def record_turn(self, *, floor_violation: bool = False, orphaned: bool = False) -> None:
        """Record turn-level metrics."""
        if floor_violation:
            self.floor_violations.record_failure()
        else:
            self.floor_violations.record_success()
        if orphaned:
            self.orphaned_turns.record_failure()
        else:
            self.orphaned_turns.record_success()

    def record_initiative_attempt(self, *, during_debt: bool) -> None:
        if during_debt:
            self.initiative_during_debt.record_failure()
        else:
            self.initiative_during_debt.record_success()

    def record_pulse_quality(
        self,
        *,
        psychologizing: bool = False,
        scratchpad_leak: bool = False,
        has_work_product: bool = True,
    ) -> None:
        if psychologizing:
            self.psychologizing.record_failure()
        else:
            self.psychologizing.record_success()
        if scratchpad_leak:
            self.scratchpad_leaks.record_failure()
        else:
            self.scratchpad_leaks.record_success()
        if has_work_product:
            self.work_product_yield.record_success()
        else:
            self.work_product_yield.record_failure()

    def record_quest_continuity(self, score: float) -> None:
        self.quest_continuity_scores.append(max(0.0, min(1.0, score)))

    def record_self_edit(self, *, passed: bool) -> None:
        if passed:
            self.self_edit_results.record_success()
        else:
            self.self_edit_results.record_failure()

    def record_rollback(self, *, success: bool) -> None:
        if success:
            self.rollback_results.record_success()
        else:
            self.rollback_results.record_failure()

    def record_kept_change(self) -> None:
        self.kept_change_count += 1

    # ── Reporting ────────────────────────────────────────────────────────────

    def report(self) -> dict:
        """Generate eval metrics report."""
        avg_continuity = (
            sum(self.quest_continuity_scores) / len(self.quest_continuity_scores)
            if self.quest_continuity_scores else 0.0
        )

        return {
            "uptime_sec": round(time.monotonic() - self._started_at, 1),
            "floor_violation_rate": round(self.floor_violations.failure_rate * 100, 2),
            "initiative_during_debt_rate": round(self.initiative_during_debt.failure_rate * 100, 2),
            "orphaned_turn_rate": round(self.orphaned_turns.failure_rate * 100, 2),
            "psychologizing_rate": round(self.psychologizing.failure_rate * 100, 2),
            "scratchpad_leak_rate": round(self.scratchpad_leaks.failure_rate * 100, 2),
            "work_product_yield": round(self.work_product_yield.rate * 100, 2),
            "quest_continuity_avg": round(avg_continuity, 3),
            "self_edit_pass_rate": round(self.self_edit_results.rate * 100, 2),
            "rollback_success_rate": round(self.rollback_results.rate * 100, 2),
            "kept_changes_24h": self.kept_change_count,
            "total_turns": self.floor_violations.total,
            "total_pulses": self.psychologizing.total,
            "total_self_edits": self.self_edit_results.total,
        }

    def check_health(self) -> tuple[bool, list[str]]:
        """Check if all metrics are within acceptable bounds.

        Returns (healthy, list_of_violations).
        """
        violations = []

        if self.floor_violations.total > 0 and self.floor_violations.failure_rate > 0:
            violations.append(f"Floor violations: {self.floor_violations.failure_rate:.1%}")

        if self.initiative_during_debt.total > 0 and self.initiative_during_debt.failure_rate > 0:
            violations.append(f"Initiative during debt: {self.initiative_during_debt.failure_rate:.1%}")

        if self.orphaned_turns.total > 0 and self.orphaned_turns.failure_rate > 0:
            violations.append(f"Orphaned turns: {self.orphaned_turns.failure_rate:.1%}")

        if self.scratchpad_leaks.total > 0 and self.scratchpad_leaks.failure_rate > 0:
            violations.append(f"Scratchpad leaks: {self.scratchpad_leaks.failure_rate:.1%}")

        if self.psychologizing.total > 10 and self.psychologizing.failure_rate > 0.05:
            violations.append(f"Psychologizing rate: {self.psychologizing.failure_rate:.1%} (target <5%)")

        if self.work_product_yield.total > 10 and self.work_product_yield.rate < 1.0:
            violations.append(f"Work product yield: {self.work_product_yield.rate:.1%} (target 100%)")

        return len(violations) == 0, violations
