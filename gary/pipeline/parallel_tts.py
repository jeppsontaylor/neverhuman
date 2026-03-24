"""
pipeline/parallel_tts.py — Non-blocking parallel TTS synthesis queue

The original server.py blocks on each sentence's TTS synthesis.
This module wraps the existing tts.py with an async queue so LLM
can keep generating while previous sentences are being synthesized.

Design:
  - asyncio.Queue receives sentences from the LLM sentence splitter
  - Worker task synthesizes and immediately sends WAV over WebSocket
  - Back-pressure: queue bounded at 8 sentences (won't OOM on long responses)
  - Cancellable: clear() drains queue + cancels in-progress synthesis

Usage:
    q = ParallelTTSQueue(ws=websocket, send_log=log_fn)
    await q.start()
    await q.enqueue("Hello there!")
    await q.enqueue("How are you?")
    await q.finish()     # wait for all queued synthesis to complete
    await q.cancel()     # or abort instantly
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Optional, Any

log = logging.getLogger("gary.parallel_tts")


class ParallelTTSQueue:
    """Async queue that synthesizes TTS sentences without blocking the LLM."""

    def __init__(
        self,
        websocket: Any,  # WebSocket connection
        send_log: Optional[Callable] = None,
        max_queue: int = 8,
    ):
        self._ws = websocket
        self._send_log = send_log
        self._queue: asyncio.Queue[Optional[str]] = asyncio.Queue(maxsize=max_queue)
        self._worker_task: Optional[asyncio.Task] = None
        self._sentences_sent = 0
        self._total_tts_ms = 0.0

    async def start(self):
        """Start the background worker. Call once per response."""
        self._sentences_sent = 0
        self._total_tts_ms = 0.0
        self._worker_task = asyncio.create_task(self._worker(), name="tts-worker")

    async def enqueue(self, sentence: str):
        """Add a sentence to the synthesis queue. Non-blocking unless queue full."""
        try:
            await asyncio.wait_for(self._queue.put(sentence), timeout=5.0)
        except asyncio.TimeoutError:
            log.warning(f"TTS queue full, dropping sentence: {sentence[:40]}...")

    async def finish(self):
        """Signal end of response and wait for all synthesis to complete."""
        await self._queue.put(None)  # sentinel
        if self._worker_task:
            try:
                await asyncio.wait_for(self._worker_task, timeout=30.0)
            except asyncio.TimeoutError:
                log.warning("TTS worker timed out after 30s")
                self._worker_task.cancel()

    async def cancel(self):
        """Cancel all pending synthesis immediately."""
        # Drain the queue
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        # Cancel the worker
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except (asyncio.CancelledError, Exception):
                pass
        self._worker_task = None

    async def _worker(self):
        """Background task: pull sentences, synthesize, send WAV."""
        from pipeline import tts  # lazy import to avoid circular deps

        while True:
            try:
                sentence = await self._queue.get()
                if sentence is None:
                    break  # sentinel = done

                t0 = time.monotonic()
                wav_bytes = await tts.synthesize(sentence)
                tts_ms = (time.monotonic() - t0) * 1000

                if wav_bytes:
                    try:
                        await self._ws.send_bytes(wav_bytes)
                        self._sentences_sent += 1
                        self._total_tts_ms += tts_ms

                        if self._send_log:
                            self._send_log(
                                "tts",
                                f"[{len(wav_bytes) // 1024}KB] {sentence[:50]}{'...' if len(sentence)>50 else ''} "
                                f"({tts_ms:.0f}ms)",
                            )
                    except Exception as e:
                        log.warning(f"Failed to send TTS audio: {e}")
                else:
                    log.debug(f"TTS returned empty for: {sentence[:40]}...")

            except asyncio.CancelledError:
                log.debug("TTS worker cancelled")
                break
            except Exception as e:
                log.warning(f"TTS worker error: {e}")

    @property
    def stats(self) -> dict:
        return {
            "sentences_sent": self._sentences_sent,
            "total_tts_ms": round(self._total_tts_ms, 1),
            "avg_tts_ms": round(self._total_tts_ms / max(1, self._sentences_sent), 1),
        }
