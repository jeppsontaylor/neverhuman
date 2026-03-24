"""
core/eval_harness.py — Evaluation harness for GARY

Captures latency, quality, and behavioral metrics from test runs.
Each run is stored in the eval_runs table with its associated
prompt version IDs for reproducibility.

10/14 reviewers requested an eval harness.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger("gary.eval_harness")


@dataclass
class EvalMetrics:
    """Metrics captured during an evaluation run."""
    ttft_ms: Optional[float] = None         # Time to first token
    ttla_ms: Optional[float] = None         # Time to last audio (full response)
    e2e_latency_ms: Optional[float] = None  # End-to-end latency
    vad_false_positive_rate: Optional[float] = None
    initiative_count: int = 0               # Number of initiatives in run
    initiative_quality: Optional[float] = None  # Manual or auto score
    retrieval_hit_rate: Optional[float] = None  # Did retrieval help?
    thought_dedup_rate: Optional[float] = None  # How often mind repeated itself
    dream_promotion_rate: Optional[float] = None  # Dreams→validated ratio

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class EvalRun:
    """A single evaluation run."""
    id: str = ""
    harness: str = "latency"        # latency | initiative | retrieval | dream_accuracy
    metrics: EvalMetrics = field(default_factory=EvalMetrics)
    prompt_version_ids: list[str] = field(default_factory=list)
    notes: str = ""
    started_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None


class EvalHarness:
    """Runs and records evaluation passes."""

    def __init__(self) -> None:
        self._runs: list[EvalRun] = []

    def start_run(
        self,
        harness: str = "latency",
        notes: str = "",
        prompt_version_ids: Optional[list[str]] = None,
    ) -> EvalRun:
        """Begin a new evaluation run."""
        from core.mind import new_thought_id
        run = EvalRun(
            id=new_thought_id(),
            harness=harness,
            notes=notes,
            prompt_version_ids=prompt_version_ids or [],
        )
        self._runs.append(run)
        log.info("Eval run started: %s (%s)", run.id, harness)
        return run

    def record_latency(
        self,
        run: EvalRun,
        ttft_ms: float,
        ttla_ms: Optional[float] = None,
        e2e_ms: Optional[float] = None,
    ) -> None:
        """Record latency metrics for a run."""
        run.metrics.ttft_ms = ttft_ms
        if ttla_ms is not None:
            run.metrics.ttla_ms = ttla_ms
        if e2e_ms is not None:
            run.metrics.e2e_latency_ms = e2e_ms

    def record_initiative(
        self,
        run: EvalRun,
        count: int,
        quality: Optional[float] = None,
    ) -> None:
        """Record initiative metrics."""
        run.metrics.initiative_count = count
        if quality is not None:
            run.metrics.initiative_quality = quality

    def record_retrieval(
        self,
        run: EvalRun,
        hit_rate: float,
    ) -> None:
        """Record retrieval effectiveness."""
        run.metrics.retrieval_hit_rate = hit_rate

    def complete_run(self, run: EvalRun) -> None:
        """Mark a run as complete."""
        run.completed_at = time.time()
        log.info(
            "Eval run completed: %s (%s) — %s",
            run.id, run.harness, run.metrics.to_dict(),
        )

    async def persist_run(self, conn, run: EvalRun) -> bool:
        """Persist a completed eval run to the database."""
        import json
        try:
            await conn.execute(
                """
                INSERT INTO eval_runs (harness, metrics, prompt_version_ids, notes)
                VALUES ($1, $2::jsonb, $3::uuid[], $4)
                """,
                run.harness,
                json.dumps(run.metrics.to_dict()),
                run.prompt_version_ids,
                run.notes,
            )
            return True
        except Exception as exc:
            log.warning("Failed to persist eval run: %s", exc)
            return False

    def get_latest(self, harness: str, n: int = 5) -> list[EvalRun]:
        """Get latest N runs for a given harness type."""
        return sorted(
            [r for r in self._runs if r.harness == harness],
            key=lambda r: r.started_at,
            reverse=True,
        )[:n]

    def compare(self, run_a: EvalRun, run_b: EvalRun) -> dict[str, Any]:
        """Compare two runs, returning deltas for each metric."""
        a = run_a.metrics.to_dict()
        b = run_b.metrics.to_dict()
        all_keys = set(a.keys()) | set(b.keys())
        deltas = {}
        for k in all_keys:
            va = a.get(k)
            vb = b.get(k)
            if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
                deltas[k] = {"before": va, "after": vb, "delta": vb - va}
            else:
                deltas[k] = {"before": va, "after": vb}
        return deltas
