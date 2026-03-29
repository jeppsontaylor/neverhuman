from __future__ import annotations

from fastapi.testclient import TestClient

from apps.selfd.serve import app


client = TestClient(app)


def test_health_endpoint():
    r = client.get("/self/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "selfd"


def test_pack_endpoint_shape():
    r = client.get("/self/pack")
    assert r.status_code == 200
    body = r.json()
    assert body["schema_version"] == 1
    assert "generated_at" in body
    assert "reflex_url" in body
    assert "llm_url" in body
    assert body["source_order"][0] == "runtime_env"
    assert "policy_cache" in body
    assert "tempo_contract" in body["policy_cache"]
    assert "turn_policy" in body["policy_cache"]
