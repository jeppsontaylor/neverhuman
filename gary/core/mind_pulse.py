"""
core/mind_pulse.py — Structured mind pulse (JSON) per notes/review*.txt

v1 contract: ephemeral inner_voice lines + typed frames + optional initiative object.
Legacy prose + [INITIATIVE: …] remains in core/mind.py until prompts switch over.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

MIND_JSON_SCHEMA_VERSION = 1

_JSON_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE | re.MULTILINE)


@dataclass
class ThoughtFrame:
    kind: str
    text: str
    salience: float = 0.5


@dataclass
class InitiativeCandidate:
    should_surface: bool
    draft: str = ""
    reason_code: str = ""


@dataclass
class MindPulse:
    schema_version: int
    inner_voice: list[str] = field(default_factory=list)
    frames: list[ThoughtFrame] = field(default_factory=list)
    initiative_candidate: Optional[InitiativeCandidate] = None


def _strip_fences(raw: str) -> str:
    s = raw.strip()
    s = _JSON_FENCE.sub("", s)
    return s.strip()


def _coerce_float(x: Any, default: float = 0.5) -> float:
    try:
        v = float(x)
        if v != v:  # NaN
            return default
        return max(0.0, min(1.0, v))
    except (TypeError, ValueError):
        return default


def format_mind_pulse_display(pulse: MindPulse) -> str:
    """Human-readable thought for dedup, history, and Mind panel."""
    lines: list[str] = []
    lines.extend(pulse.inner_voice)
    for fr in pulse.frames:
        lines.append(f"[{fr.kind}] {fr.text}")
    return "\n".join(lines).strip() or "(empty pulse)"


def score_mind_pulse(pulse: MindPulse, phase: str) -> float:
    """Salience 0–1 from structured pulse (phase label = reflecting|brainstorming|dreaming)."""
    s = 0.35
    s += min(0.25, len(pulse.frames) * 0.05)
    if pulse.frames:
        s += 0.15 * max(fr.salience for fr in pulse.frames)
    if pulse.inner_voice:
        s += min(0.1, 0.03 * len(pulse.inner_voice))
    ic = pulse.initiative_candidate
    if ic and ic.should_surface and ic.draft.strip():
        s += 0.15
    if phase == "dreaming":
        s += 0.08
    elif phase == "brainstorming":
        s += 0.04
    return float(min(1.0, s))


def parse_mind_pulse_json(raw: str) -> Optional[MindPulse]:
    """Parse a mind model response as MindPulse v1. Returns None if not valid JSON v1."""
    if not raw or not raw.strip():
        return None
    try:
        data = json.loads(_strip_fences(raw))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    ver = data.get("schema_version")
    try:
        ver = int(ver)
    except (TypeError, ValueError):
        return None
    if ver != MIND_JSON_SCHEMA_VERSION:
        return None

    iv = data.get("inner_voice", [])
    if iv is None:
        inner_voice: list[str] = []
    elif isinstance(iv, str):
        inner_voice = [iv] if iv.strip() else []
    elif isinstance(iv, list):
        inner_voice = [str(x).strip() for x in iv if str(x).strip()]
    else:
        return None

    frames_in = data.get("frames", [])
    frames: list[ThoughtFrame] = []
    if frames_in is None:
        pass
    elif not isinstance(frames_in, list):
        return None
    else:
        for item in frames_in:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind", "other")).strip() or "other"
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            frames.append(
                ThoughtFrame(
                    kind=kind,
                    text=text,
                    salience=_coerce_float(item.get("salience"), 0.5),
                )
            )

    init_raw = data.get("initiative_candidate")
    initiative: Optional[InitiativeCandidate] = None
    if init_raw is not None:
        if not isinstance(init_raw, dict):
            return None
        initiative = InitiativeCandidate(
            should_surface=bool(init_raw.get("should_surface", False)),
            draft=str(init_raw.get("draft", "") or "").strip(),
            reason_code=str(init_raw.get("reason_code", "") or "").strip(),
        )

    return MindPulse(
        schema_version=ver,
        inner_voice=inner_voice,
        frames=frames,
        initiative_candidate=initiative,
    )


# Future prompt appendix (not wired into build_mind_prompt yet):
MIND_JSON_V1_INSTRUCTION = """
Output format: return ONE JSON object only (no markdown fences), schema_version MUST be 1:
{
  "schema_version": 1,
  "inner_voice": ["1-3 short private lines"],
  "frames": [
    {"kind": "question|insight|repair|followup|hypothesis|experiment|boundary|other", "text": "...", "salience": 0.0-1.0}
  ],
  "initiative_candidate": null or {"should_surface": false, "draft": "", "reason_code": ""}
}
Imagined content stays in frames with kind appropriate to the phase; do not assert facts without evidence.
"""
