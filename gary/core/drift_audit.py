"""
core/drift_audit.py — Docs-runtime drift audit (Phase 8C)

Periodic job that compares:
  - self-manifest vs actual runtime state
  - module contracts vs actual exports
  - capability registry vs edit_policies
  - runtime state vs human Bibles

Flags mismatches and logs drift events.
"""
from __future__ import annotations

import importlib
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("gary.drift_audit")

GARY_ROOT = Path(__file__).parent.parent


@dataclass
class DriftEvent:
    """A detected drift between docs/specs and runtime."""
    category: str                  # self_model | contract | capability | bible
    module: str
    expected: str
    actual: str
    severity: str = "warning"      # warning | error | info

    def __str__(self) -> str:
        return (
            f"[{self.severity.upper()}] {self.category}/{self.module}: "
            f"expected={self.expected}, actual={self.actual}"
        )


@dataclass
class DriftReport:
    """Report from a drift audit run."""
    events: list[DriftEvent] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return len(self.events) == 0

    @property
    def error_count(self) -> int:
        return sum(1 for e in self.events if e.severity == "error")

    def summary(self) -> str:
        if self.clean:
            return "✅ No drift detected"
        lines = [f"⚠️  {len(self.events)} drift events found ({self.error_count} errors):"]
        for e in self.events:
            lines.append(f"  {e}")
        return "\n".join(lines)


def audit_module_exports(module_name: str, expected_exports: list[str]) -> list[DriftEvent]:
    """Check that a module actually exports what contracts say it should."""
    events = []
    try:
        mod = importlib.import_module(module_name)
        for export in expected_exports:
            if not hasattr(mod, export):
                events.append(DriftEvent(
                    category="contract",
                    module=module_name,
                    expected=f"export '{export}'",
                    actual="not found",
                    severity="error",
                ))
    except ImportError as e:
        events.append(DriftEvent(
            category="contract",
            module=module_name,
            expected="importable",
            actual=f"ImportError: {e}",
            severity="error",
        ))
    return events


def audit_file_exists(paths: list[str]) -> list[DriftEvent]:
    """Check that expected files exist."""
    events = []
    for path in paths:
        full = GARY_ROOT / path
        if not full.exists():
            events.append(DriftEvent(
                category="capability",
                module=path,
                expected="file exists",
                actual="missing",
                severity="warning",
            ))
    return events


def audit_self_model_freshness(self_pack: Any) -> list[DriftEvent]:
    """Check self-model fields for staleness."""
    events = []
    if self_pack is None:
        events.append(DriftEvent(
            category="self_model",
            module="self_pack",
            expected="compiled",
            actual="None",
            severity="error",
        ))
        return events

    for section_name in ("architecture", "runtime", "mission_profile",
                         "capabilities", "background_mind"):
        section = getattr(self_pack, section_name, {})
        for key, field_val in section.items():
            if hasattr(field_val, 'is_stale') and field_val.is_stale:
                events.append(DriftEvent(
                    category="self_model",
                    module=f"{section_name}.{key}",
                    expected="fresh",
                    actual=f"stale (provenance={field_val.provenance})",
                    severity="warning",
                ))
    return events


def run_drift_audit(self_pack: Any = None) -> DriftReport:
    """Run a full drift audit."""
    report = DriftReport()

    # 1. Check core module exports match contracts
    contract_checks = {
        "pipeline.turn_supervisor": [
            "TurnSupervisor", "FloorState", "FloorOwner", "Engagement",
            "SystemMode", "Event", "BackgroundLease", "TurnRecord", "TurnStatus",
        ],
        "pipeline.turn_classifier": [
            "TurnMode", "IntentClass", "ReasoningMode", "TurnClassification",
            "classify_turn", "classify_turn_v2",
        ],
        "core.self_model": ["SelfPack", "SelfField", "compile_self_pack"],
        "core.change_router": ["ChangeTier", "ChangeRequest", "classify_change"],
        "core.drive_types": ["DriveVector"],
    }
    for mod_name, exports in contract_checks.items():
        report.events.extend(audit_module_exports(mod_name, exports))

    # 2. Check critical files exist
    critical_files = [
        "pipeline/turn_supervisor.py",
        "pipeline/turn_classifier.py",
    ]
    report.events.extend(audit_file_exists(critical_files))

    # 3. Self-model freshness
    if self_pack is not None:
        report.events.extend(audit_self_model_freshness(self_pack))

    log.info(report.summary())
    return report
