"""testing/test_context_pack.py — Context compiler v1"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from pipeline.context_pack import compile_reflex_context, context_pack_enabled


def test_disabled_returns_same_history(monkeypatch):
    monkeypatch.delenv("GARY_CONTEXT_PACK", raising=False)
    h = [{"role": "user", "content": "hi"}]
    out, ph = compile_reflex_context(h, enabled=False)
    assert out == h
    assert ph == ""


def test_enabled_prepends_pack(monkeypatch):
    monkeypatch.setenv("GARY_CONTEXT_PACK", "1")
    h = [
        {"role": "assistant", "content": "Hello."},
        {"role": "user", "content": "What's up?"},
    ]
    out, ph = compile_reflex_context(h)
    assert len(out) == len(h) + 1
    assert out[0]["role"] == "system"
    assert "Context pack v2" in out[0]["content"]
    assert "LAST_USER:" in out[0]["content"]
    assert "What's up?" in out[0]["content"]
    assert "PRIOR_ASSISTANT:" in out[0]["content"]
    assert "Hello" in out[0]["content"]
    assert len(ph) == 20
    assert out[1:] == h


def test_hash_stable(monkeypatch):
    monkeypatch.setenv("GARY_CONTEXT_PACK", "1")
    h = [{"role": "user", "content": "x"}]
    _, a = compile_reflex_context(h)
    _, b = compile_reflex_context(h)
    assert a == b


def test_context_pack_enabled(monkeypatch):
    monkeypatch.delenv("GARY_CONTEXT_PACK", raising=False)
    assert context_pack_enabled() is False
    monkeypatch.setenv("GARY_CONTEXT_PACK", "1")
    assert context_pack_enabled() is True
