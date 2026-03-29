"""apps/selfd/serve.py — Runtime self-model service.

Provides lightweight, runtime-grounded capability snapshots for self-queries.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass, asdict
from typing import Literal, Optional

from fastapi import FastAPI
from pipeline.turn_policy import tempo_cache_info, turn_policy_cache_info


Confidence = Literal["observed", "configured", "inferred", "planned"]


@dataclass
class SelfField:
    value: str
    confidence: Confidence


@dataclass
class SelfPack:
    schema_version: int
    generated_at: float
    reflex_url: SelfField
    llm_url: SelfField
    mind_mode: SelfField
    context_pack_enabled: SelfField
    source_order: list[str]
    policy_cache: dict
    signature: Optional[str] = None


def _safe_port(value: str, default: str) -> str:
    try:
        iv = int(value)
    except Exception:
        return default
    if 1 <= iv <= 65535:
        return str(iv)
    return default


def _sign_pack(pack: SelfPack, signing_key: str) -> str:
    payload = asdict(pack)
    payload["signature"] = None
    msg = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(signing_key.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def build_self_pack(now: float | None = None) -> SelfPack:
    now = now if now is not None else time.time()
    reflex_port = _safe_port(os.getenv("PORT", "7861"), "7861")
    llm_port = _safe_port(os.getenv("LLM_PORT", "8088"), "8088")
    signing_key = os.getenv("GARY_SELFD_SIGNING_KEY", "").strip()

    pack = SelfPack(
        schema_version=1,
        generated_at=now,
        reflex_url=SelfField(f"https://localhost:{reflex_port}", "configured"),
        llm_url=SelfField(f"http://localhost:{llm_port}", "configured"),
        mind_mode=SelfField(
            "remote" if os.getenv("GARY_MIND_REMOTE", "0") == "1" else "embedded",
            "configured",
        ),
        context_pack_enabled=SelfField(
            "true" if os.getenv("GARY_CONTEXT_PACK", "0") == "1" else "false",
            "configured",
        ),
        source_order=["runtime_env", "config", "docs"],
        policy_cache={
            "tempo_contract": tempo_cache_info(),
            "turn_policy": turn_policy_cache_info(),
        },
    )
    if signing_key:
        pack.signature = _sign_pack(pack, signing_key)
    return pack


app = FastAPI(title="GARY selfd", version="0.1.0")


@app.get("/self/health")
async def health() -> dict:
    return {"status": "ok", "service": "selfd"}


@app.get("/self/pack")
async def self_pack() -> dict:
    return asdict(build_self_pack())
