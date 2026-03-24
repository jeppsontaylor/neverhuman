"""
memory/event_writer.py — Fire-and-forget async event outbox (v5.0)

Bridge between Reflex Core and Memory Spine. Now uses a two-stage pipeline:
  1. Sync append to local spool (never loses data)
  2. Async drain from spool → Postgres

The voice pipeline NEVER blocks waiting for a database write.

Design:
  - EventSpool handles durable local persistence (sync, fsync'd)
  - Background flusher drains spool → Postgres in batches
  - If DB is unavailable, spool accumulates locally (replay on restart)
  - Events are GaryEvent objects from core/events.py

Usage:
    writer = EventWriter()
    await writer.start()
    writer.emit(UtteranceEvent.from_transcript("s1", "hello", 1.0, 100))
    # ... later
    await writer.stop()
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

from memory.spool import EventSpool

log = logging.getLogger("gary.event_writer")


class EventWriter:
    """Fire-and-forget event writer. Two-stage: spool → Postgres."""

    def __init__(self, spool_dir: str = "/tmp/gary/spool",
                 flush_interval: float = 1.0):
        self._spool = EventSpool(spool_dir=spool_dir, flush_interval=flush_interval)
        self._stats = {"emitted": 0, "spool_errors": 0}

    async def start(self):
        """Start the background spool flusher."""
        await self._spool.start_flusher()
        log.info("Event writer started (spool-backed)")

    async def stop(self):
        """Flush remaining events and stop."""
        await self._spool.stop()
        log.info(f"Event writer stopped. Stats: {self._stats}, spool: {self._spool.stats}")

    def emit(self, event) -> bool:
        """
        Queue an event for async persistence. Non-blocking; spool is crash-safe
        (see memory/spool.py for reboot vs process-crash semantics).
        Event is written synchronously to local spool, then drained to DB.
        Returns True if spooled, False on spool write failure.
        """
        ok = self._spool.append(event)
        if ok:
            self._stats["emitted"] += 1
        else:
            self._stats["spool_errors"] += 1
        return ok

    @property
    def stats(self) -> dict:
        return {**self._stats, "spool": self._spool.stats}

    @property
    def pending_count(self) -> int:
        return self._spool.pending_count
