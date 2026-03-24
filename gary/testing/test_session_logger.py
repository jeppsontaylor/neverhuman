"""
testing/test_session_logger.py — Tests for the dual-tier session logger

Validates:
  - File creation in temp dir
  - JSONL format correctness
  - Rotation triggers at size threshold
  - Condensed log format (ISO timestamp, correct fields)
  - Queue drain on close()
  - Disabled mode (no files created)
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.session_logger import SessionLogger, find_latest_log


# ── Helpers ────────────────────────────────────────────────────────────────────

def _read_jsonl(path: Path) -> list[dict]:
    """Read all lines from a JSONL file and parse as JSON."""
    lines = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                lines.append(json.loads(line))
    return lines


# ── Tests ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_log_dir():
    """Provide a temp directory for session logs."""
    with tempfile.TemporaryDirectory(prefix="gary_test_log_") as d:
        yield Path(d)


class TestSessionLoggerBasic:
    """Core functionality: file creation, JSONL format, both tiers."""

    @pytest.mark.asyncio
    async def test_creates_files(self, tmp_log_dir, monkeypatch):
        monkeypatch.setenv("GARY_SESSION_LOG", "1")
        logger = SessionLogger(session_id="test001", log_dir=tmp_log_dir)
        await logger.start()

        logger.log("test_event", "sys", {"foo": "bar"})
        logger.log_condensed("user", "said", "hello", {"asr_ms": 100})

        await logger.close()

        detailed = tmp_log_dir / "detailed_test001.jsonl"
        condensed = tmp_log_dir / "condensed_test001.jsonl"
        assert detailed.exists(), "Detailed log file should exist"
        assert condensed.exists(), "Condensed log file should exist"

    @pytest.mark.asyncio
    async def test_detailed_jsonl_format(self, tmp_log_dir, monkeypatch):
        monkeypatch.setenv("GARY_SESSION_LOG", "1")
        logger = SessionLogger(session_id="fmt001", log_dir=tmp_log_dir)
        await logger.start()

        logger.log("transcript", "asr", {"text": "hello world", "asr_ms": 120}, turn=1)
        logger.log("llm_token", "llm", {"token": " Hello"}, turn=1)

        await logger.close()

        records = _read_jsonl(tmp_log_dir / "detailed_fmt001.jsonl")
        assert len(records) == 2

        r = records[0]
        assert r["event"] == "transcript"
        assert r["source"] == "asr"
        assert r["session_id"] == "fmt001"
        assert r["turn"] == 1
        assert r["data"]["text"] == "hello world"
        assert "ts" in r
        assert "ts_mono" in r

    @pytest.mark.asyncio
    async def test_condensed_jsonl_format(self, tmp_log_dir, monkeypatch):
        monkeypatch.setenv("GARY_SESSION_LOG", "1")
        logger = SessionLogger(session_id="cnd001", log_dir=tmp_log_dir)
        await logger.start()

        logger.log_condensed("user", "said", "How are you?", {"asr_ms": 80}, turn=2)
        logger.log_condensed("gary", "replied", "I'm doing great!", {"total_ms": 500}, turn=2)

        await logger.close()

        records = _read_jsonl(tmp_log_dir / "condensed_cnd001.jsonl")
        assert len(records) == 2

        r = records[0]
        assert r["actor"] == "user"
        assert r["action"] == "said"
        assert r["text"] == "How are you?"
        assert r["turn"] == 2
        assert "T" in r["ts"]  # ISO-8601 format contains 'T'

    @pytest.mark.asyncio
    async def test_multiple_events_batched(self, tmp_log_dir, monkeypatch):
        monkeypatch.setenv("GARY_SESSION_LOG", "1")
        logger = SessionLogger(session_id="batch001", log_dir=tmp_log_dir)
        await logger.start()

        for i in range(50):
            logger.log("llm_token", "llm", {"token": f"tok_{i}"}, turn=1)

        await logger.close()

        records = _read_jsonl(tmp_log_dir / "detailed_batch001.jsonl")
        assert len(records) == 50


class TestSessionLoggerDisabled:
    """When GARY_SESSION_LOG is 0, nothing should happen."""

    @pytest.mark.asyncio
    async def test_no_files_when_disabled(self, tmp_log_dir, monkeypatch):
        monkeypatch.setenv("GARY_SESSION_LOG", "0")
        logger = SessionLogger(session_id="nope001", log_dir=tmp_log_dir)
        await logger.start()

        logger.log("test_event", "sys", {"foo": "bar"})
        logger.log_condensed("user", "said", "hello", {})

        await logger.close()

        # No files should be created
        files = list(tmp_log_dir.iterdir())
        assert len(files) == 0, f"Expected no files, got: {files}"


class TestSessionLoggerRotation:
    """File rotation when exceeding MAX_FILE_SIZE."""

    @pytest.mark.asyncio
    async def test_rotation_on_size(self, tmp_log_dir, monkeypatch):
        monkeypatch.setenv("GARY_SESSION_LOG", "1")
        monkeypatch.setenv("GARY_SESSION_LOG_MAX_MB", "0")  # 0 MB = rotate on every write

        # Reimport to pick up new env var — but we can just set the module constant
        import core.session_logger as sl
        original_max = sl.MAX_FILE_SIZE
        sl.MAX_FILE_SIZE = 100  # 100 bytes → forces rotation quickly

        try:
            logger = SessionLogger(session_id="rot001", log_dir=tmp_log_dir)
            await logger.start()

            # Write enough events to trigger rotation
            for i in range(20):
                logger.log("test_event", "sys", {"data": f"payload_{i}" * 10}, turn=i)

            await logger.close()

            # Should have multiple segment files
            detailed_files = sorted(tmp_log_dir.glob("detailed_rot001*"))
            assert len(detailed_files) >= 2, f"Expected rotation, got {len(detailed_files)} files: {detailed_files}"
        finally:
            sl.MAX_FILE_SIZE = original_max


class TestSessionLoggerStats:
    """Stats tracking."""

    @pytest.mark.asyncio
    async def test_stats_tracked(self, tmp_log_dir, monkeypatch):
        monkeypatch.setenv("GARY_SESSION_LOG", "1")
        logger = SessionLogger(session_id="stats001", log_dir=tmp_log_dir)
        await logger.start()

        logger.log("e1", "sys", {})
        logger.log("e2", "sys", {})
        logger.log_condensed("user", "said", "hi", {})

        await logger.close()

        assert logger.stats["detailed_events"] == 2
        assert logger.stats["condensed_events"] == 1
        assert logger.stats["write_errors"] == 0


class TestFindLatestLog:
    """Module-level helper for REST endpoint."""

    def test_find_latest_returns_most_recent(self, tmp_log_dir, monkeypatch):
        monkeypatch.setenv("GARY_SESSION_LOG_DIR", str(tmp_log_dir))

        # Create two log files with different mtimes
        f1 = tmp_log_dir / "condensed_aaa.jsonl"
        f1.write_text('{"test": 1}\n')

        import time
        time.sleep(0.05)  # ensure different mtime

        f2 = tmp_log_dir / "condensed_bbb.jsonl"
        f2.write_text('{"test": 2}\n')

        result = find_latest_log("condensed")
        assert result is not None
        assert result.name == "condensed_bbb.jsonl"

    def test_find_latest_returns_none_when_empty(self, tmp_log_dir, monkeypatch):
        monkeypatch.setenv("GARY_SESSION_LOG_DIR", str(tmp_log_dir))
        result = find_latest_log("condensed")
        assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
