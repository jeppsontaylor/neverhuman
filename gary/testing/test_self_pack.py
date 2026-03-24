"""
testing/test_self_pack.py — Tests for the self-model compiler
"""
import time
from core.self_model import SelfPack, SelfField, compile_self_pack


class TestSelfField:
    """SelfField tracks provenance and staleness."""

    def test_field_provenance(self):
        f = SelfField("hello", "live_runtime")
        assert f.provenance == "live_runtime"
        assert f.value == "hello"

    def test_field_not_stale_initially(self):
        f = SelfField("hello", "live_runtime", stale_after_sec=300)
        assert not f.is_stale

    def test_field_stale_after_timeout(self):
        f = SelfField("hello", "live_runtime", stale_after_sec=0.01)
        time.sleep(0.02)
        assert f.is_stale


class TestSelfPack:
    """SelfPack compiles a truthful self-manifest."""

    def test_compile_basic(self):
        pack = compile_self_pack(start_time=time.monotonic())
        assert "version" in pack.architecture
        assert pack.architecture["version"].value == "4.1"

    def test_compile_includes_process_id(self):
        pack = compile_self_pack(start_time=time.monotonic())
        assert "process_id" in pack.architecture
        assert pack.architecture["process_id"].provenance == "live_runtime"

    def test_compile_includes_git_sha(self):
        pack = compile_self_pack(start_time=time.monotonic())
        assert "current_commit" in pack.architecture

    def test_compile_with_quests(self):
        quests = [{"id": "q1", "title": "Test quest"}]
        pack = compile_self_pack(start_time=time.monotonic(), active_quests=quests)
        assert pack.runtime["active_quests"].value == quests

    def test_compile_with_mission_overrides(self):
        overrides = {"domain_focus": "science", "initiative_style": "proactive"}
        pack = compile_self_pack(start_time=time.monotonic(), mission_overrides=overrides)
        assert pack.mission_profile["domain_focus"].value == "science"

    def test_summary_for_prompt(self):
        pack = compile_self_pack(start_time=time.monotonic())
        summary = pack.summary_for_prompt()
        assert "[SELF-MODEL]" in summary
        assert "architecture" in summary
        assert "4.1" in summary

    def test_to_context_dict(self):
        pack = compile_self_pack(start_time=time.monotonic())
        d = pack.to_context_dict()
        assert "architecture" in d
        assert "runtime" in d
        assert "version" in d["architecture"]
        assert d["architecture"]["version"]["provenance"] == "manifest"

    def test_capabilities(self):
        pack = compile_self_pack(start_time=time.monotonic())
        assert pack.capabilities["self_edit"].value is True
        assert pack.capabilities["voice_conversation"].value is True
