from __future__ import annotations

from apps.selfd.serve import build_self_pack


def test_self_pack_shape():
    pack = build_self_pack(now=123.0)
    assert pack.schema_version == 1
    assert pack.generated_at == 123.0
    assert pack.reflex_url.value.startswith("https://localhost:")
    assert pack.llm_url.value.startswith("http://localhost:")
    assert pack.mind_mode.confidence == "configured"
    assert pack.context_pack_enabled.confidence == "configured"


def test_source_order_is_runtime_first():
    pack = build_self_pack(now=456.0)
    assert pack.source_order[0] == "runtime_env"
    assert "tempo_contract" in pack.policy_cache
    assert "turn_policy" in pack.policy_cache


def test_signature_added_when_key_set(monkeypatch):
    monkeypatch.setenv("GARY_SELFD_SIGNING_KEY", "abc123")
    pack = build_self_pack(now=1.0)
    assert pack.signature is not None
    assert len(pack.signature) == 64


def test_invalid_port_falls_back(monkeypatch):
    monkeypatch.setenv("PORT", "99999")
    monkeypatch.setenv("LLM_PORT", "-1")
    pack = build_self_pack(now=2.0)
    assert pack.reflex_url.value.endswith(":7861")
    assert pack.llm_url.value.endswith(":8088")
