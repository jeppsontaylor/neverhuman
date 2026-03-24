"""
testing/test_mind_pulse.py — Mind JSON pulse parser (v1)
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from core.mind_pulse import (
    MIND_JSON_SCHEMA_VERSION,
    parse_mind_pulse_json,
    MindPulse,
)


def test_parse_valid_minimal():
    raw = """
    {
      "schema_version": 1,
      "inner_voice": ["Line one.", "Line two."],
      "frames": [{"kind": "question", "text": "What did they really need?", "salience": 0.8}],
      "initiative_candidate": null
    }
    """
    p = parse_mind_pulse_json(raw)
    assert p is not None
    assert p.schema_version == MIND_JSON_SCHEMA_VERSION
    assert len(p.inner_voice) == 2
    assert len(p.frames) == 1
    assert p.frames[0].kind == "question"
    assert p.initiative_candidate is None


def test_parse_with_fence():
    raw = """```json
{"schema_version": 1, "inner_voice": [], "frames": [], "initiative_candidate": null}
```"""
    p = parse_mind_pulse_json(raw)
    assert p is not None
    assert p.frames == []


def test_parse_initiative_candidate():
    raw = """{
      "schema_version": 1,
      "inner_voice": [],
      "frames": [],
      "initiative_candidate": {"should_surface": true, "draft": "Quick check-in?", "reason_code": "open_loop"}
    }"""
    p = parse_mind_pulse_json(raw)
    assert p is not None
    assert p.initiative_candidate is not None
    assert p.initiative_candidate.should_surface is True
    assert "check-in" in p.initiative_candidate.draft


def test_invalid_json_returns_none():
    assert parse_mind_pulse_json("not json {{{") is None


def test_wrong_schema_version():
    raw = '{"schema_version": 2, "inner_voice": [], "frames": []}'
    assert parse_mind_pulse_json(raw) is None


def test_inner_voice_string_coerced():
    raw = '{"schema_version": 1, "inner_voice": "solo", "frames": []}'
    p = parse_mind_pulse_json(raw)
    assert p is not None
    assert p.inner_voice == ["solo"]


def test_skips_empty_frame_text():
    raw = """{"schema_version": 1, "inner_voice": [], "frames": [
      {"kind": "x", "text": ""},
      {"kind": "y", "text": "ok"}
    ]}"""
    p = parse_mind_pulse_json(raw)
    assert p is not None
    assert len(p.frames) == 1
    assert p.frames[0].text == "ok"
