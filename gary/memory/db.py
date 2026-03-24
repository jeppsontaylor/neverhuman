"""
memory/db.py — Async Postgres connection pool for GARY v2

Provides a global asyncpg pool with LISTEN/NOTIFY support.
Uses environment variable DATABASE_URL or sensible defaults.

Usage:
    from memory.db import get_pool, close_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM events LIMIT 10")
    await close_pool()
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Callable, Coroutine, Optional

log = logging.getLogger("gary.db")

_pool = None
_DEFAULT_DSN = os.getenv(
    "GARY_DATABASE_URL",
    f"postgresql://gary:{os.getenv('GARY_DB_PASS', 'changeme')}@localhost:5432/gary"
)


async def get_pool():
    """Get or create the global asyncpg connection pool."""
    global _pool
    if _pool is not None:
        return _pool

    try:
        import asyncpg
    except ImportError:
        log.error("asyncpg not installed. Run: pip install asyncpg")
        raise

    dsn = os.environ.get("DATABASE_URL", _DEFAULT_DSN)
    _pool = await asyncpg.create_pool(
        dsn,
        min_size=2,
        max_size=10,
        command_timeout=30,
        statement_cache_size=100,
    )
    log.info(f"Postgres pool ready ({_pool.get_size()} connections)")
    return _pool


async def close_pool():
    """Close the global connection pool."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        log.info("Postgres pool closed")


async def listen_events(
    callback: Callable[[dict], Coroutine],
    channel: str = "gary_events",
):
    """
    Subscribe to LISTEN/NOTIFY on the events channel.
    Calls callback(payload_dict) for each notification.

    This should run as a background task.
    """
    try:
        import asyncpg
    except ImportError:
        log.error("asyncpg not installed")
        return

    dsn = os.environ.get("DATABASE_URL", _DEFAULT_DSN)
    conn = await asyncpg.connect(dsn)

    try:
        await conn.add_listener(channel, lambda *args: _dispatch(callback, args))
        log.info(f"Listening on '{channel}'")
        # Keep alive
        while True:
            await asyncio.sleep(3600)
    finally:
        await conn.close()


def _dispatch(callback, args):
    """Parse NOTIFY payload and dispatch to async callback."""
    # args = (connection, pid, channel, payload)
    if len(args) >= 4:
        try:
            payload = json.loads(args[3])
            asyncio.get_event_loop().create_task(callback(payload))
        except json.JSONDecodeError:
            log.warning(f"Invalid NOTIFY payload: {args[3]}")


async def ensure_partition(year: int, month: int):
    """Create an events partition for the given month if it doesn't exist."""
    pool = await get_pool()
    table = f"events_{year}_{month:02d}"
    next_month = month + 1
    next_year = year
    if next_month > 12:
        next_month = 1
        next_year = year + 1

    sql = f"""
    CREATE TABLE IF NOT EXISTS {table} PARTITION OF events
        FOR VALUES FROM ('{year}-{month:02d}-01')
        TO ('{next_year}-{next_month:02d}-01');
    """
    async with pool.acquire() as conn:
        try:
            await conn.execute(sql)
            log.info(f"Partition {table} ready")
        except Exception as e:
            # Partition may already exist
            log.debug(f"Partition {table}: {e}")
