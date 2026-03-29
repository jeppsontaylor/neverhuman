from __future__ import annotations

import os
import stat

from pipeline.turn_classifier import TurnMode
from pipeline.turn_policy import build_turn_policy, turn_policy_cache_info


def test_snap_policy_has_small_budget_and_no_filler():
    p = build_turn_policy("yes")
    assert p.turn_mode == TurnMode.SNAP
    assert p.llm_max_tokens == 120
    assert p.should_play_filler is False


def test_deep_policy_has_large_budget():
    p = build_turn_policy("please implement a robust architecture in detail")
    assert p.turn_mode == TurnMode.DEEP
    assert p.llm_max_tokens == 800
    assert p.llm_temperature == 0.72


def test_explore_policy_maps_to_deep_mode():
    p = build_turn_policy("brainstorm a novel counterfactual experiment")
    assert p.tempo_contract.mode == "explore"
    assert p.turn_mode == TurnMode.DEEP
    assert p.llm_max_tokens == 700


def test_policy_turn_contract_serializable_shape():
    p = build_turn_policy("what time is it")
    assert p.tempo_contract.mode in {"snap", "quick", "deep", "explore"}
    assert p.tempo_contract.first_sentence_max_words > 0


def test_turn_policy_cache_hits_increase():
    before = turn_policy_cache_info()["hits"]
    build_turn_policy("this line should be cached")
    build_turn_policy("this line should be cached")
    after = turn_policy_cache_info()["hits"]
    assert after >= before + 1


def test_turn_policy_uses_rust_binary_when_available(tmp_path, monkeypatch):
    script = tmp_path / "tempo_policy_bin"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json,sys\n"
        "inp=json.loads(sys.stdin.read())\n"
        "print(json.dumps({"
        "\"turn_mode\":\"layered\","
        "\"tempo_contract\":{"
        "\"mode\":\"quick\",\"context_pack\":\"standard\",\"model_route\":\"sidecar_then_35b\","
        "\"first_sentence_max_words\":10,\"max_sentences\":3,\"answer_first\":True,\"progressive_tts\":True"
        "},"
        "\"llm_max_tokens\":320,\"llm_temperature\":0.7,\"should_play_filler\":True"
        "}))\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("GARY_TEMPO_BIN", os.fspath(script))

    p = build_turn_policy("hello there")
    assert p.turn_mode == TurnMode.LAYERED
    assert p.llm_max_tokens == 320
