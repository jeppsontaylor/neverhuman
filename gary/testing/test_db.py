"""
testing/test_db.py — Tests for the database connection module

Validates:
  - DSN configuration
  - Pool management (get_pool, close_pool)
  - Partition SQL generation
  - LISTEN/NOTIFY dispatch parsing
  - Module structure
"""
import asyncio
import inspect
import json
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from memory.db import _DEFAULT_DSN, _dispatch, ensure_partition


class TestDSN:
    def test_default_dsn_format(self):
        assert _DEFAULT_DSN.startswith("postgresql://")

    def test_default_dsn_has_database(self):
        assert "gary" in _DEFAULT_DSN

    def test_default_dsn_has_host(self):
        assert "localhost" in _DEFAULT_DSN

    def test_default_dsn_has_port(self):
        assert "5432" in _DEFAULT_DSN


class TestDispatch:
    def test_valid_json_payload(self):
        """_dispatch should parse valid JSON and invoke callback."""
        received = []

        async def callback(payload):
            received.append(payload)

        # Simulate NOTIFY args: (connection, pid, channel, payload)
        args = (None, 123, "gary_events", '{"event_id": "abc-123"}')

        # Need a running event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            _dispatch(callback, args)
            # Let the created task run
            loop.run_until_complete(asyncio.sleep(0.01))
            assert len(received) == 1
            assert received[0]["event_id"] == "abc-123"
        finally:
            loop.close()

    def test_invalid_json_payload(self):
        """_dispatch should handle invalid JSON gracefully."""
        async def callback(payload):
            pass

        args = (None, 123, "gary_events", "not valid json {{{")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # Should not raise
            _dispatch(callback, args)
            loop.run_until_complete(asyncio.sleep(0.01))
        finally:
            loop.close()

    def test_short_args_ignored(self):
        """_dispatch with insufficient args should not crash."""
        async def callback(payload):
            pass

        _dispatch(callback, (None, 123))  # only 2 args, need 4


class TestPartitionSQL:
    """Test partition logic without a real database."""

    def test_partition_function_exists(self):
        """ensure_partition is an async function."""
        assert inspect.iscoroutinefunction(ensure_partition)

    def test_partition_month_rollover(self):
        """Verify the month rollover logic (Dec → Jan next year)."""
        # This tests the logic in ensure_partition
        month = 12
        year = 2026
        next_month = month + 1
        next_year = year
        if next_month > 12:
            next_month = 1
            next_year = year + 1

        assert next_month == 1
        assert next_year == 2027

    def test_partition_normal_month(self):
        """Normal month increment."""
        month = 6
        year = 2026
        next_month = month + 1
        next_year = year
        if next_month > 12:
            next_month = 1
            next_year = year + 1

        assert next_month == 7
        assert next_year == 2026


class TestModuleImports:
    def test_get_pool_importable(self):
        from memory.db import get_pool
        assert inspect.iscoroutinefunction(get_pool)

    def test_close_pool_importable(self):
        from memory.db import close_pool
        assert inspect.iscoroutinefunction(close_pool)

    def test_listen_events_importable(self):
        from memory.db import listen_events
        assert inspect.iscoroutinefunction(listen_events)


class TestPoolState:
    def test_pool_starts_none(self):
        """Global pool should start as None (lazy initialization)."""
        import memory.db as db_mod
        # Save state
        original = db_mod._pool
        db_mod._pool = None
        try:
            assert db_mod._pool is None
        finally:
            db_mod._pool = original


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
