"""Rust-first tempo + turn policy wrapper.

All deterministic tempo/policy logic should live in Rust.
Python remains a thin transport layer with compact fallback logic.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, asdict
from functools import lru_cache
from typing import Literal

from pipeline.turn_classifier import TurnMode

TempoMode = Literal["snap", "quick", "deep", "explore"]
ContextPack = Literal["micro", "standard", "deep"]
ModelRoute = Literal["sidecar_only", "sidecar_then_35b", "main35b_direct"]


@dataclass(frozen=True)
class TurnContract:
    mode: TempoMode
    context_pack: ContextPack
    model_route: ModelRoute
    first_sentence_max_words: int
    max_sentences: int
    answer_first: bool = True
    progressive_tts: bool = True


@dataclass(frozen=True)
class TurnPolicy:
    turn_mode: TurnMode
    tempo_contract: TurnContract
    llm_max_tokens: int
    llm_temperature: float
    should_play_filler: bool


def _tempo_bin() -> str:
    return os.getenv("GARY_TEMPO_BIN", "")


def contract_to_dict(contract: TurnContract) -> dict:
    return asdict(contract)


def _fallback_mode(text: str) -> TempoMode:
    t = text.strip().lower()
    if not t:
        return "snap"
    if any(k in t for k in ("brainstorm", "counterfactual", "hypothesis", "novel")):
        return "explore"
    if any(k in t for k in ("implement", "architecture", "step by step", "debug")) or len(t.split()) >= 32:
        return "deep"
    if len(t.split()) <= 5:
        return "snap"
    return "quick"


def _fallback_contract(text: str) -> TurnContract:
    mode = _fallback_mode(text)
    if mode == "snap":
        return TurnContract(mode, "micro", "sidecar_only", 7, 2)
    if mode == "quick":
        return TurnContract(mode, "standard", "sidecar_then_35b", 10, 3)
    if mode == "deep":
        return TurnContract(mode, "deep", "main35b_direct", 12, 6)
    return TurnContract(mode, "deep", "sidecar_then_35b", 12, 5)


def to_turn_mode(contract: TurnContract) -> TurnMode:
    return TurnMode.SNAP if contract.mode == "snap" else (TurnMode.LAYERED if contract.mode == "quick" else TurnMode.DEEP)


def llm_params_for_contract(contract: TurnContract) -> tuple[int, float]:
    return {"snap": (120, 0.55), "quick": (320, 0.70), "deep": (800, 0.72), "explore": (700, 0.90)}[contract.mode]


@lru_cache(maxsize=512)
def _build_turn_contract_cached(text: str, has_external_lookup: bool, tempo_bin: str) -> TurnContract:
    if tempo_bin:
        try:
            payload = json.dumps({"text": text, "has_external_lookup": has_external_lookup})
            res = subprocess.run([tempo_bin], input=payload, text=True, capture_output=True, check=True, timeout=0.3)
            return TurnContract(**json.loads(res.stdout))
        except Exception:
            pass
    return _fallback_contract(text)


def build_turn_contract(text: str, has_external_lookup: bool = False) -> TurnContract:
    return _build_turn_contract_cached(text, has_external_lookup, _tempo_bin())


@lru_cache(maxsize=512)
def _build_turn_policy_cached(text: str, tempo_bin: str) -> TurnPolicy:
    if tempo_bin:
        try:
            payload = json.dumps({"command": "policy", "text": text})
            res = subprocess.run([tempo_bin], input=payload, text=True, capture_output=True, check=True, timeout=0.3)
            obj = json.loads(res.stdout)
            return TurnPolicy(
                turn_mode=TurnMode(obj["turn_mode"]),
                tempo_contract=TurnContract(**obj["tempo_contract"]),
                llm_max_tokens=int(obj["llm_max_tokens"]),
                llm_temperature=float(obj["llm_temperature"]),
                should_play_filler=bool(obj["should_play_filler"]),
            )
        except Exception:
            pass

    contract = build_turn_contract(text)
    mode = to_turn_mode(contract)
    max_tokens, temp = llm_params_for_contract(contract)
    return TurnPolicy(mode, contract, max_tokens, temp, mode != TurnMode.SNAP)


def build_turn_policy(text: str) -> TurnPolicy:
    return _build_turn_policy_cached(text, _tempo_bin())


def tempo_cache_info() -> dict:
    i = _build_turn_contract_cached.cache_info()
    return {"hits": i.hits, "misses": i.misses, "maxsize": i.maxsize, "currsize": i.currsize}


def turn_policy_cache_info() -> dict:
    i = _build_turn_policy_cached.cache_info()
    return {"hits": i.hits, "misses": i.misses, "maxsize": i.maxsize, "currsize": i.currsize}
