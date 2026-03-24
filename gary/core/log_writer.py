"""
core/log_writer.py — Thread-safe append-only log writer for persistent session logs.

Writes to three files in GARY/logs/:
  - conversation.log   (User / GARY dialogue)
  - mind_stream.log    (internal monologue with phase labels)
  - websocket.log      (all pipeline events)

File handles stay open for the process lifetime.  A threading.Lock
guards writes so the async mind-loop and the main websocket handler
can call `append()` concurrently without interleaving.
"""

import io
import threading
import time
from pathlib import Path

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"

_handles: dict[str, io.TextIOWrapper] = {}
_lock = threading.Lock()


def _get(name: str):
    """Return (or lazily open) the file handle for the given log name."""
    if name not in _handles:
        LOGS_DIR.mkdir(exist_ok=True)
        _handles[name] = open(LOGS_DIR / f"{name}.log", "a", encoding="utf-8")
    return _handles[name]


def _ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def append(name: str, line: str):
    """Append a timestamped line to the named log file. Thread-safe."""
    with _lock:
        f = _get(name)
        f.write(f"[{_ts()}] {line}\n")
        f.flush()
