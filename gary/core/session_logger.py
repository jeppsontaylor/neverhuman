"""
core/session_logger.py — Dual-tier async JSONL session logger

Captures every pipeline event into two JSONL files per session:
  1. detailed_<session>.jsonl — every token, VAD prob, think block, timing
  2. condensed_<session>.jsonl — user text, agent replies, timestamps, key timings

Design:
  - Sync log() / log_condensed() enqueue to an asyncio.Queue (never block voice)
  - Background _writer_task drains queue in batches (up to 64 events / 200ms)
  - File rotation at MAX_FILE_SIZE_MB (default 50 MB)
  - Opt-in via GARY_SESSION_LOG=1 env var

Usage:
    logger = SessionLogger(session_id="abc123")
    await logger.start()
    logger.log("transcript", "asr", {"text": "hello", "asr_ms": 120}, turn=1)
    logger.log_condensed("user", "said", "hello", {"asr_ms": 120}, turn=1)
    await logger.close()
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger("gary.session_logger")

# ── Configuration ─────────────────────────────────────────────────────────────
_DEFAULT_LOG_DIR = Path(__file__).resolve().parent.parent / "logs" / "sessions"
MAX_FILE_SIZE_MB = int(os.getenv("GARY_SESSION_LOG_MAX_MB", "50"))
MAX_FILE_SIZE = MAX_FILE_SIZE_MB * 1024 * 1024

# Batch parameters for write-coalescing
_BATCH_SIZE = 64
_BATCH_WAIT_SEC = 0.2


def is_enabled() -> bool:
    """Check if session logging is enabled via environment (default: on)."""
    return os.getenv("GARY_SESSION_LOG", "1").strip().lower() in ("1", "true", "yes", "on")


def get_log_dir() -> Path:
    """Return the configured log directory."""
    custom = os.getenv("GARY_SESSION_LOG_DIR", "").strip()
    return Path(custom) if custom else _DEFAULT_LOG_DIR


def _detailed_enabled() -> bool:
    """Check if detailed logging tier is enabled (default: on when logging on)."""
    val = os.getenv("GARY_SESSION_LOG_DETAILED", "1").strip().lower()
    return val in ("1", "true", "yes", "on")


class SessionLogger:
    """
    Fire-and-forget dual-tier JSONL session logger.

    All public log methods are synchronous and non-blocking — they push
    to an asyncio.Queue which a background task drains in write-coalesced
    batches.
    """

    def __init__(self, session_id: str, log_dir: Optional[Path] = None):
        self._session_id = session_id
        self._log_dir = log_dir or get_log_dir()
        self._started = False
        self._closed = False

        # Queue for async drain
        self._queue: asyncio.Queue = asyncio.Queue()
        self._writer_task: Optional[asyncio.Task] = None

        # File handles
        self._detailed_fd: Optional[Any] = None
        self._condensed_fd: Optional[Any] = None

        # Rotation counters
        self._detailed_seg = 0
        self._condensed_seg = 0
        self._detailed_bytes = 0
        self._condensed_bytes = 0

        # Stats
        self._stats = {"detailed_events": 0, "condensed_events": 0, "write_errors": 0}

        # Monotonic reference for ts_mono deltas
        self._t0_mono = time.monotonic()

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self):
        """Create log directory and launch background writer."""
        if not is_enabled():
            return
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._open_files()
        self._writer_task = asyncio.create_task(self._writer_loop(), name="session-logger")
        self._started = True
        log.info(f"Session logger started: {self._log_dir} (session={self._session_id})")

    async def close(self):
        """Flush remaining events and close file handles."""
        if not self._started or self._closed:
            return
        self._closed = True

        # Signal writer to stop
        await self._queue.put(None)  # sentinel

        if self._writer_task:
            try:
                await asyncio.wait_for(self._writer_task, timeout=5.0)
            except asyncio.TimeoutError:
                log.warning("Session logger writer timed out on close")
                self._writer_task.cancel()
            except asyncio.CancelledError:
                pass

        self._close_files()
        log.info(f"Session logger closed. Stats: {self._stats}")

    # ── Public logging API (sync, non-blocking) ───────────────────────────────

    def log(self, event: str, source: str, data: dict, *, turn: int = 0):
        """
        Log a detailed event. Non-blocking — pushes to async queue.

        Args:
            event:  Event type string (e.g. 'transcript', 'llm_token')
            source: Pipeline stage ('asr', 'llm', 'tts', 'vad', 'mind', 'sys', 'user')
            data:   Event-specific payload dict
            turn:   Current turn epoch
        """
        if not self._started or self._closed:
            return
        if not _detailed_enabled():
            return

        record = {
            "ts": time.time(),
            "ts_mono": round(time.monotonic() - self._t0_mono, 4),
            "session_id": self._session_id,
            "turn": turn,
            "event": event,
            "source": source,
            "data": data,
        }
        try:
            self._queue.put_nowait(("detailed", record))
        except Exception:
            self._stats["write_errors"] += 1

    def log_condensed(self, actor: str, action: str, text: str, meta: dict, *, turn: int = 0):
        """
        Log a condensed (study-grade) event. Non-blocking.

        Args:
            actor:  'user', 'gary', 'mind', 'system'
            action: Short verb — 'said', 'replied', 'thought', 'interrupted', etc.
            text:   Full text content
            meta:   Key timings and metadata
            turn:   Current turn epoch
        """
        if not self._started or self._closed:
            return

        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session_id": self._session_id,
            "turn": turn,
            "actor": actor,
            "action": action,
            "text": text,
            "meta": meta,
        }
        try:
            self._queue.put_nowait(("condensed", record))
        except Exception:
            self._stats["write_errors"] += 1

    # ── File management ───────────────────────────────────────────────────────

    def _detailed_path(self, seg: int = 0) -> Path:
        suffix = f".{seg}" if seg > 0 else ""
        return self._log_dir / f"detailed_{self._session_id}{suffix}.jsonl"

    def _condensed_path(self, seg: int = 0) -> Path:
        suffix = f".{seg}" if seg > 0 else ""
        return self._log_dir / f"condensed_{self._session_id}{suffix}.jsonl"

    def _open_files(self):
        """Open (or reopen) file handles for current segments."""
        try:
            if _detailed_enabled():
                p = self._detailed_path(self._detailed_seg)
                self._detailed_fd = open(p, "a", buffering=8192)  # 8KB buffer for coalescing
                self._detailed_bytes = p.stat().st_size if p.exists() else 0
        except Exception as e:
            log.error(f"Failed to open detailed log: {e}")

        try:
            p = self._condensed_path(self._condensed_seg)
            self._condensed_fd = open(p, "a", buffering=8192)
            self._condensed_bytes = p.stat().st_size if p.exists() else 0
        except Exception as e:
            log.error(f"Failed to open condensed log: {e}")

    def _close_files(self):
        """Flush and close all open file handles."""
        for fd in (self._detailed_fd, self._condensed_fd):
            if fd:
                try:
                    fd.flush()
                    fd.close()
                except Exception:
                    pass
        self._detailed_fd = None
        self._condensed_fd = None

    def _rotate_if_needed(self, tier: str):
        """Rotate the file if it exceeds MAX_FILE_SIZE."""
        if tier == "detailed":
            if self._detailed_bytes >= MAX_FILE_SIZE and self._detailed_fd:
                self._detailed_fd.flush()
                self._detailed_fd.close()
                self._detailed_seg += 1
                p = self._detailed_path(self._detailed_seg)
                self._detailed_fd = open(p, "a", buffering=8192)
                self._detailed_bytes = 0
                log.info(f"Rotated detailed log → segment {self._detailed_seg}")
        else:
            if self._condensed_bytes >= MAX_FILE_SIZE and self._condensed_fd:
                self._condensed_fd.flush()
                self._condensed_fd.close()
                self._condensed_seg += 1
                p = self._condensed_path(self._condensed_seg)
                self._condensed_fd = open(p, "a", buffering=8192)
                self._condensed_bytes = 0
                log.info(f"Rotated condensed log → segment {self._condensed_seg}")

    # ── Background writer ─────────────────────────────────────────────────────

    async def _writer_loop(self):
        """
        Drain queue in batches, write-coalesced.
        Batches up to _BATCH_SIZE events or _BATCH_WAIT_SEC, whichever comes first.
        """
        while True:
            batch: list[tuple[str, dict]] = []
            try:
                # Block until at least one event arrives
                item = await self._queue.get()
                if item is None:
                    break  # sentinel — shut down
                batch.append(item)

                # Drain up to _BATCH_SIZE more without waiting long
                deadline = time.monotonic() + _BATCH_WAIT_SEC
                while len(batch) < _BATCH_SIZE:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        item = await asyncio.wait_for(self._queue.get(), timeout=remaining)
                        if item is None:
                            # Sentinel arrived mid-batch — write what we have then stop
                            self._write_batch(batch)
                            return
                        batch.append(item)
                    except asyncio.TimeoutError:
                        break

                self._write_batch(batch)

            except asyncio.CancelledError:
                # Drain anything remaining
                while not self._queue.empty():
                    try:
                        item = self._queue.get_nowait()
                        if item is not None:
                            batch.append(item)
                    except Exception:
                        break
                if batch:
                    self._write_batch(batch)
                return
            except Exception as exc:
                log.error(f"Session logger writer error: {exc}")
                self._stats["write_errors"] += 1
                await asyncio.sleep(0.5)

    def _write_batch(self, batch: list[tuple[str, dict]]):
        """Write a batch of events to appropriate files."""
        detailed_lines: list[str] = []
        condensed_lines: list[str] = []

        for tier, record in batch:
            try:
                line = json.dumps(record, default=str, ensure_ascii=False) + "\n"
                if tier == "detailed":
                    detailed_lines.append(line)
                    self._stats["detailed_events"] += 1
                else:
                    condensed_lines.append(line)
                    self._stats["condensed_events"] += 1
            except Exception as exc:
                log.debug(f"JSON serialize error: {exc}")
                self._stats["write_errors"] += 1

        # Write detailed
        if detailed_lines and self._detailed_fd:
            try:
                payload = "".join(detailed_lines)
                self._detailed_fd.write(payload)
                self._detailed_fd.flush()
                self._detailed_bytes += len(payload.encode("utf-8"))
                self._rotate_if_needed("detailed")
            except Exception as exc:
                log.error(f"Detailed log write error: {exc}")
                self._stats["write_errors"] += 1

        # Write condensed
        if condensed_lines and self._condensed_fd:
            try:
                payload = "".join(condensed_lines)
                self._condensed_fd.write(payload)
                self._condensed_fd.flush()
                self._condensed_bytes += len(payload.encode("utf-8"))
                self._rotate_if_needed("condensed")
            except Exception as exc:
                log.error(f"Condensed log write error: {exc}")
                self._stats["write_errors"] += 1

    # ── Introspection ─────────────────────────────────────────────────────────

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def log_dir(self) -> Path:
        return self._log_dir


# ── Module-level helpers for server.py REST endpoints ─────────────────────────

def find_latest_log(log_type: str = "condensed") -> Optional[Path]:
    """
    Find the most recently modified log file of the given type.
    Returns None if no matching files exist.
    """
    log_dir = get_log_dir()
    if not log_dir.exists():
        return None

    prefix = f"{log_type}_"
    candidates = sorted(
        (f for f in log_dir.iterdir() if f.name.startswith(prefix) and f.suffix == ".jsonl"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None
