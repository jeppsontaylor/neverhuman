from __future__ import annotations

import os
import stat

from pipeline.turn_policy import (
    build_turn_contract,
    contract_to_dict,
    llm_params_for_contract,
    tempo_cache_info,
    to_turn_mode,
)
from pipeline.turn_classifier import TurnMode


def test_snap_contract_for_short_turn():
    c = build_turn_contract("yes")
    assert c.mode == "snap"
    assert c.context_pack == "micro"
    assert c.answer_first is True


def test_deep_contract_for_complex_turn():
    c = build_turn_contract("Can you implement an architecture decision engine in detail")
    assert c.mode == "deep"
    assert c.model_route in {"main35b_direct", "sidecar_then_35b"}


def test_explore_contract_for_novelty_turn():
    c = build_turn_contract("brainstorm a counterfactual hypothesis for this design")
    assert c.mode == "explore"


def test_contract_to_dict_shape():
    c = build_turn_contract("what time is it")
    d = contract_to_dict(c)
    assert set(d.keys()) == {
        "mode",
        "context_pack",
        "model_route",
        "first_sentence_max_words",
        "max_sentences",
        "answer_first",
        "progressive_tts",
    }


def test_to_turn_mode_mapping():
    assert to_turn_mode(build_turn_contract("yes")) == TurnMode.SNAP
    assert to_turn_mode(build_turn_contract("please implement the design in detail")) == TurnMode.DEEP
    assert to_turn_mode(build_turn_contract("brainstorm a novel hypothesis")) == TurnMode.DEEP


def test_llm_params_for_contract():
    assert llm_params_for_contract(build_turn_contract("yes")) == (120, 0.55)
    assert llm_params_for_contract(build_turn_contract("please implement the architecture in detail")) == (800, 0.72)
    assert llm_params_for_contract(build_turn_contract("brainstorm a novel counterfactual")) == (700, 0.9)


def test_tempo_contract_cache_hits_increase():
    before = tempo_cache_info()["hits"]
    build_turn_contract("cache me maybe")
    build_turn_contract("cache me maybe")
    after = tempo_cache_info()["hits"]
    assert after >= before + 1


def test_uses_rust_binary_when_available(tmp_path, monkeypatch):
    script = tmp_path / "tempo_bin"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json,sys\n"
        "inp=json.loads(sys.stdin.read())\n"
        "print(json.dumps({"
        "\"mode\":\"quick\",\"context_pack\":\"standard\",\"model_route\":\"sidecar_then_35b\","
        "\"first_sentence_max_words\":10,\"max_sentences\":3,\"answer_first\":True,\"progressive_tts\":True"
        "}))\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("GARY_TEMPO_BIN", os.fspath(script))

    c = build_turn_contract("anything")
    assert c.mode == "quick"
