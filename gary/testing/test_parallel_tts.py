"""
testing/test_parallel_tts.py — Tests for the parallel TTS synthesis queue

Tests the queue mechanics without requiring actual TTS models.
Uses mock synthesize() to verify:
  - Queue start/enqueue/finish flow
  - Cancellation drains queue
  - Stats accumulation
  - Sentinel handling
"""
import asyncio
import pytest
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline.parallel_tts import ParallelTTSQueue


class MockWebSocket:
    """Fake WebSocket that records sent bytes."""
    def __init__(self):
        self.sent = []

    async def send_bytes(self, data: bytes):
        self.sent.append(data)


@pytest.fixture
def ws():
    return MockWebSocket()


@pytest.fixture
def log_calls():
    calls = []
    def log_fn(svc, msg):
        calls.append((svc, msg))
    return calls, log_fn


class TestParallelTTSQueue:
    @pytest.mark.asyncio
    async def test_enqueue_and_finish(self, ws, log_calls):
        calls, log_fn = log_calls
        q = ParallelTTSQueue(websocket=ws, send_log=log_fn)

        # Mock TTS to return fake WAV bytes
        fake_wav = b"RIFF" + b"\x00" * 100

        with patch("pipeline.tts.synthesize", new_callable=AsyncMock, return_value=fake_wav):
            await q.start()
            await q.enqueue("Hello world.")
            await q.enqueue("How are you?")
            await q.finish()

        assert len(ws.sent) == 2
        assert q.stats["sentences_sent"] == 2

    @pytest.mark.asyncio
    async def test_cancel_drains_queue(self, ws):
        q = ParallelTTSQueue(websocket=ws)

        # Use a slow mock to simulate synthesis
        async def slow_synth(text):
            await asyncio.sleep(10)  # very slow — will be cancelled
            return b"RIFF" + b"\x00" * 100

        with patch("pipeline.tts.synthesize", side_effect=slow_synth):
            await q.start()
            await q.enqueue("sentence 1")
            await q.enqueue("sentence 2")
            await q.enqueue("sentence 3")
            # Cancel immediately
            await q.cancel()

        # Queue should be drained, few or no sentences sent
        assert q.stats["sentences_sent"] <= 1

    @pytest.mark.asyncio
    async def test_empty_tts_result_not_sent(self, ws):
        q = ParallelTTSQueue(websocket=ws)

        with patch("pipeline.tts.synthesize", new_callable=AsyncMock, return_value=b""):
            await q.start()
            await q.enqueue("Empty result sentence.")
            await q.finish()

        assert len(ws.sent) == 0
        assert q.stats["sentences_sent"] == 0

    @pytest.mark.asyncio
    async def test_stats_timing(self, ws):
        q = ParallelTTSQueue(websocket=ws)
        fake_wav = b"RIFF" + b"\x00" * 100

        with patch("pipeline.tts.synthesize", new_callable=AsyncMock, return_value=fake_wav):
            await q.start()
            await q.enqueue("Test.")
            await q.finish()

        stats = q.stats
        assert stats["sentences_sent"] == 1
        assert stats["total_tts_ms"] >= 0
        assert stats["avg_tts_ms"] >= 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
