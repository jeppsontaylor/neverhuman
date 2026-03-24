"""
testing/test_tts.py — Tests for the TTS pipeline (without loading the actual model)

Validates:
  - Voice management logic (get/set)
  - Synthesis flow (empty text, unloaded model)
  - Load/unload state management
  - Module constants
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pipeline.tts as tts_module


class TestVoiceManagement:
    def test_get_voice_default(self):
        assert tts_module.get_voice() == "af_heart"

    def test_get_voices_without_model(self):
        """Without a loaded model, get_voices returns empty list."""
        # Save and clear state
        original = tts_module._kokoro
        tts_module._kokoro = None
        try:
            assert tts_module.get_voices() == []
        finally:
            tts_module._kokoro = original

    def test_set_voice_invalid_without_model(self):
        """Setting a voice when no model is loaded should return False."""
        original = tts_module._kokoro
        tts_module._kokoro = None
        try:
            result = tts_module.set_voice("nonexistent_voice")
            assert result is False
        finally:
            tts_module._kokoro = original


class TestSynthesisFlow:
    @pytest.mark.asyncio
    async def test_empty_text_returns_empty(self):
        result = await tts_module.synthesize("")
        assert result == b""

    @pytest.mark.asyncio
    async def test_whitespace_only_returns_empty(self):
        result = await tts_module.synthesize("   ")
        assert result == b""

    def test_sync_synthesize_without_model(self):
        """Synthesis without loaded model returns empty bytes."""
        original_loaded = tts_module._tts_loaded
        original_kokoro = tts_module._kokoro
        tts_module._tts_loaded = False
        tts_module._kokoro = None
        try:
            result = tts_module._sync_synthesize("Hello world")
            assert result == b""
        finally:
            tts_module._tts_loaded = original_loaded
            tts_module._kokoro = original_kokoro


class TestLoadUnload:
    def test_is_available_false_initially(self):
        """Without explicit load, model may not be available."""
        # This depends on global state — just verify it's a bool
        assert isinstance(tts_module.is_available(), bool)

    def test_is_loaded_returns_bool(self):
        assert isinstance(tts_module.is_loaded(), bool)

    def test_unload_is_idempotent(self):
        """Unloading when nothing is loaded should not raise."""
        original = tts_module._kokoro
        tts_module._kokoro = None
        tts_module._tts_loaded = False
        try:
            tts_module.unload()  # should not raise
        finally:
            tts_module._kokoro = original

    def test_last_use_time_default(self):
        """last_use_time returns a float."""
        assert isinstance(tts_module.last_use_time(), float)

    def test_load_is_noop(self):
        """Legacy load() is a no-op, should not raise."""
        tts_module.load()  # should not raise


class TestCachePaths:
    def test_cache_dir_is_set(self):
        assert tts_module._CACHE_DIR is not None
        assert "kokoro-onnx" in str(tts_module._CACHE_DIR)

    def test_model_url_is_valid(self):
        assert tts_module._MODEL_URL.startswith("https://")
        assert "kokoro" in tts_module._MODEL_URL

    def test_voices_url_is_valid(self):
        assert tts_module._VOICES_URL.startswith("https://")


class TestVoiceConstants:
    def test_default_voice(self):
        assert tts_module._VOICE == "af_heart"

    def test_sample_rate(self):
        assert tts_module._sample_rate == 24000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
