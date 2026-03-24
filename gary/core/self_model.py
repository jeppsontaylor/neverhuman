"""
core/self_model.py — SELF_PACK compiler + runtime probes

Builds a truthful self-manifest from live runtime state, NOT from docs.
Truth hierarchy: runtime probes > machine manifests > code atlas > doc annotations.

Every field carries provenance: live_runtime | manifest | code_atlas | doc_annotation.
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger("gary.self_model")


@dataclass
class SelfField:
    """A self-model field with provenance tracking."""
    value: Any
    provenance: str = "doc_annotation"   # live_runtime | manifest | code_atlas | doc_annotation
    stale_after_sec: float = 300.0       # consider stale after this many seconds
    last_refreshed: float = field(default_factory=time.monotonic)

    @property
    def is_stale(self) -> bool:
        return (time.monotonic() - self.last_refreshed) > self.stale_after_sec


@dataclass
class SelfPack:
    """Runtime-first self-manifest."""
    architecture: dict[str, SelfField] = field(default_factory=dict)
    mission_profile: dict[str, SelfField] = field(default_factory=dict)
    runtime: dict[str, SelfField] = field(default_factory=dict)
    capabilities: dict[str, SelfField] = field(default_factory=dict)
    background_mind: dict[str, SelfField] = field(default_factory=dict)

    def to_context_dict(self) -> dict:
        """Flatten into a dict suitable for LLM context packing."""
        result: dict[str, Any] = {}
        for section_name in ("architecture", "mission_profile", "runtime",
                             "capabilities", "background_mind"):
            section = getattr(self, section_name)
            result[section_name] = {
                k: {
                    "value": v.value,
                    "provenance": v.provenance,
                    "stale": v.is_stale,
                }
                for k, v in section.items()
            }
        return result

    def summary_for_prompt(self) -> str:
        """Compact text summary for injecting into LLM prompts."""
        lines = ["[SELF-MODEL]"]
        for section_name in ("architecture", "mission_profile", "runtime",
                             "capabilities", "background_mind"):
            section = getattr(self, section_name)
            if not section:
                continue
            lines.append(f"  {section_name}:")
            for k, v in section.items():
                stale = " (STALE)" if v.is_stale else ""
                lines.append(f"    {k}: {v.value} [{v.provenance}]{stale}")
        return "\n".join(lines)


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(__file__),
            text=True,
            timeout=2,
        ).strip()
    except Exception:
        return "unknown"


def _get_uptime(start_time: float) -> float:
    return time.monotonic() - start_time


def compile_self_pack(
    *,
    version: str = "4.1",
    start_time: float = 0.0,
    active_quests: Optional[list[dict]] = None,
    loaded_models: Optional[list[str]] = None,
    active_lanes: Optional[list[str]] = None,
    voice_setting: str = "default",
    mission_overrides: Optional[dict] = None,
) -> SelfPack:
    """Compile a fresh SelfPack from available runtime state.

    Called at startup and after deploys/live-setting changes.
    """
    pack = SelfPack()

    # ── Architecture (live probes) ───────────────────────────────────────────
    pack.architecture["version"] = SelfField(version, "manifest")
    pack.architecture["current_commit"] = SelfField(_git_sha(), "live_runtime")
    pack.architecture["process_id"] = SelfField(os.getpid(), "live_runtime")

    # ── Runtime ──────────────────────────────────────────────────────────────
    pack.runtime["uptime_sec"] = SelfField(
        round(_get_uptime(start_time), 1), "live_runtime", stale_after_sec=60
    )
    pack.runtime["voice"] = SelfField(voice_setting, "live_runtime")
    pack.runtime["models_loaded"] = SelfField(
        loaded_models or ["unknown"], "live_runtime", stale_after_sec=600
    )
    pack.runtime["active_quests"] = SelfField(
        active_quests or [], "live_runtime", stale_after_sec=120
    )

    # ── Mission ──────────────────────────────────────────────────────────────
    overrides = mission_overrides or {}
    pack.mission_profile["domain_focus"] = SelfField(
        overrides.get("domain_focus", "general"), "manifest"
    )
    pack.mission_profile["initiative_style"] = SelfField(
        overrides.get("initiative_style", "balanced"), "manifest"
    )
    pack.mission_profile["curiosity_appetite"] = SelfField(
        overrides.get("curiosity_appetite", 0.5), "manifest"
    )

    # ── Capabilities ─────────────────────────────────────────────────────────
    pack.capabilities["self_edit"] = SelfField(True, "manifest")
    pack.capabilities["voice_conversation"] = SelfField(True, "live_runtime")
    pack.capabilities["background_cognition"] = SelfField(True, "manifest")
    pack.capabilities["quest_board"] = SelfField(True, "manifest")

    # ── Background mind ──────────────────────────────────────────────────────
    pack.background_mind["active_lanes"] = SelfField(
        active_lanes or [], "live_runtime", stale_after_sec=60
    )

    return pack
