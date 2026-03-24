"""
core/change_router.py — Change escalation ladder

Classifies user change requests into the correct tier:
  live_setting  → DB override, push to browser
  mission_change → Update mission_profile in DB, adjust DriveVector
  prompt_change → Warm-reload prompt overlay
  code_patch    → Forge workflow
  architecture_change → Forge + elevated approval
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ChangeTier(str, Enum):
    LIVE_SETTING        = "live_setting"         # instant via DB, no restart
    MISSION_CHANGE      = "mission_change"       # updates DriveVector + mission profile
    PROMPT_CHANGE       = "prompt_change"         # warm-reload prompt overlay
    CODE_PATCH          = "code_patch"            # Forge workflow
    ARCHITECTURE_CHANGE = "architecture_change"   # Forge + elevated approval


@dataclass
class ChangeRequest:
    tier: ChangeTier
    key: str                   # e.g. "theme.background", "mission.domain_focus"
    description: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    needs_confirmation: bool = False
    needs_reboot: bool = False

    @property
    def is_code_change(self) -> bool:
        return self.tier in (ChangeTier.CODE_PATCH, ChangeTier.ARCHITECTURE_CHANGE)


# ── Default routing table ────────────────────────────────────────────────────

_LIVE_SETTING_KEYS = {
    "background", "color", "theme", "voice", "speed", "tone",
    "response_speed", "volume", "style", "disclosure_level",
    "mind_disclosure", "thoughts",
}

_MISSION_KEYS = {
    "focus", "mission", "goal", "priority", "interest",
    "proactive", "curious", "creative", "initiative",
}


def classify_change(transcript: str) -> ChangeRequest:
    """Classify a user change request into the correct tier.

    Uses deterministic keyword matching. No LLM call.
    """
    lower = transcript.lower().strip()

    # Check for code/architecture changes first (most restrictive)
    if any(k in lower for k in ("add a command", "add a feature", "add a new",
                                 "modify your code", "modify the code",
                                 "edit yourself", "patch yourself", "rewrite",
                                 "update yourself")):
        if any(k in lower for k in ("turn detection", "audio pipeline", "architecture")):
            return ChangeRequest(
                tier=ChangeTier.ARCHITECTURE_CHANGE,
                key="architecture",
                description=transcript,
                needs_confirmation=True,
                needs_reboot=True,
            )
        return ChangeRequest(
            tier=ChangeTier.CODE_PATCH,
            key="code",
            description=transcript,
            needs_confirmation=True,
            needs_reboot=True,
        )

    # Check for mission changes
    if any(k in lower for k in _MISSION_KEYS):
        return ChangeRequest(
            tier=ChangeTier.MISSION_CHANGE,
            key="mission_profile",
            description=transcript,
        )

    # Check for live settings
    if any(k in lower for k in _LIVE_SETTING_KEYS):
        return ChangeRequest(
            tier=ChangeTier.LIVE_SETTING,
            key=_extract_setting_key(lower),
            description=transcript,
        )

    # Default to live setting for simple "change X" requests
    return ChangeRequest(
        tier=ChangeTier.LIVE_SETTING,
        key="general",
        description=transcript,
    )


def _extract_setting_key(lower: str) -> str:
    """Extract the setting key from a lowercased request."""
    for key in _LIVE_SETTING_KEYS:
        if key in lower:
            return f"settings.{key}"
    return "settings.general"
