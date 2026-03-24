"""
memory/spool.py — Local append-only event spool (crash-safe)

The critical safety net between the Reflex Core and the Memory Spine.
Events are written SYNCHRONOUSLY to a local binary file before being
drained into Postgres asynchronously.

If Postgres is unavailable or the **process** crashes, the spool file survives
and is replayed on next application startup. Default directory `/tmp/gary/spool`
does not survive **OS reboot**; use a durable path for reboot-safe buffering.

Design:
  - Each event is one line of JSON + newline (JSONL format)
  - File is opened in append mode with OS-level flush
  - Background flusher drains spool → Postgres
  - After successful DB write, spool entries are marked as consumed
  - Spool is rotated/truncated when fully consumed

Usage:
    spool = EventSpool("/tmp/gary/spool")
    spool.append(event)                          # sync, never fails
    await spool.start_flusher()                  # background drain loop
    await spool.stop()
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("gary.spool")

# Default spool directory
DEFAULT_SPOOL_DIR = "/tmp/gary/spool"


class EventSpool:
    """
    Append-only local event spool. Survives process crash if the spool
    directory persists; see module docstring for reboot vs crash semantics.
    """

    def __init__(self, spool_dir: str = DEFAULT_SPOOL_DIR, flush_interval: float = 1.0):
        self._spool_dir = Path(spool_dir)
        self._spool_dir.mkdir(parents=True, exist_ok=True)
        self._active_file = self._spool_dir / "active.jsonl"
        self._flush_interval = flush_interval
        self._flusher: Optional[asyncio.Task] = None
        self._stats = {"appended": 0, "flushed": 0, "errors": 0}
        self._fd = None

    def append(self, event) -> bool:
        """
        Synchronously append an event to the local spool.
        This is called from the hot path — it must be fast and never fail.
        Returns True on success.
        """
        try:
            if isinstance(event, dict):
                data = event
            elif hasattr(event, "model_dump"):
                data = event.model_dump()
            elif hasattr(event, "__dict__"):
                data = event.__dict__
            else:
                data = {"raw": str(event)}

            # Ensure serializable
            line = json.dumps(data, default=str) + "\n"

            # Append mode, OS-level flush
            if self._fd is None:
                self._fd = open(self._active_file, "a", buffering=1)  # line-buffered
            self._fd.write(line)
            self._fd.flush()
            os.fsync(self._fd.fileno())

            self._stats["appended"] += 1
            return True
        except Exception as e:
            self._stats["errors"] += 1
            log.error(f"Spool append failed: {e}")
            return False

    async def start_flusher(self):
        """Start the background flusher that drains spool → Postgres."""
        self._flusher = asyncio.create_task(self._flush_loop(), name="spool-flusher")
        log.info(f"Spool flusher started, dir={self._spool_dir}")

    async def stop(self):
        """Stop the flusher and close the spool file."""
        if self._flusher:
            self._flusher.cancel()
            try:
                await self._flusher
            except asyncio.CancelledError:
                pass
            self._flusher = None
        if self._fd:
            self._fd.close()
            self._fd = None
        log.info(f"Spool stopped. Stats: {self._stats}")

    async def _flush_loop(self):
        """Background loop: read spool lines and write them to Postgres in batches."""
        from memory.db import get_pool

        while True:
            try:
                await asyncio.sleep(self._flush_interval)

                # Read all pending lines
                if not self._active_file.exists():
                    continue

                lines = self._read_pending()
                if not lines:
                    continue

                # Parse and write to Postgres
                events = []
                for line in lines:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        log.warning(f"Skipping malformed spool line: {line[:80]}")

                if events:
                    try:
                        pool = await get_pool()
                        async with pool.acquire() as conn:
                            await self._insert_batch(conn, events)
                        self._stats["flushed"] += len(events)
                    except Exception as e:
                        self._stats["errors"] += 1
                        log.error(f"Spool flush to DB failed: {e}")
                        continue  # Don't truncate — retry next cycle

                # Truncate the spool after successful flush
                self._truncate_spool()

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Spool flush loop error: {e}")
                await asyncio.sleep(5.0)  # back off on unexpected errors

    def _read_pending(self) -> list:
        """Read all lines from the spool file."""
        # Close the write fd temporarily so we can read cleanly
        if self._fd:
            self._fd.flush()

        try:
            with open(self._active_file, "r") as f:
                lines = [l.strip() for l in f if l.strip()]
            return lines
        except Exception:
            return []

    def _truncate_spool(self):
        """Truncate the spool file after successful flush."""
        try:
            if self._fd:
                self._fd.close()
                self._fd = None
            # Truncate by opening in write mode
            with open(self._active_file, "w") as f:
                pass
            # Reopen in append mode
            self._fd = open(self._active_file, "a", buffering=1)
        except Exception as e:
            log.error(f"Spool truncate failed: {e}")

    async def _insert_batch(self, conn, events: list):
        """Insert a batch of spool events into Postgres."""
        sql = """
        INSERT INTO events (id, ts, session_id, session_seq, turn_id,
                           actor, kind, payload, artifact_id, parent_id, epistemic_status)
        VALUES ($1, to_timestamp($2), $3, $4, $5, $6, $7, $8::jsonb, $9::uuid, $10::uuid, $11)
        ON CONFLICT (id) DO NOTHING
        """
        records = []
        for d in events:
            records.append((
                d.get("id"),
                d.get("ts", time.time()),
                d.get("session_id", ""),
                d.get("session_seq", 0),
                d.get("turn_id", ""),
                d.get("actor", "system"),
                d.get("kind", "session"),
                json.dumps(d.get("payload", {})),
                d.get("artifact_id"),
                d.get("parent_id"),
                d.get("epistemic_status", "observed"),
            ))
        await conn.executemany(sql, records)

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    @property
    def pending_count(self) -> int:
        """Approximate number of unflushed events."""
        try:
            if self._active_file.exists():
                return sum(1 for _ in open(self._active_file))
            return 0
        except Exception:
            return 0
