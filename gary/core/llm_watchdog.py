"""
core/llm_watchdog.py — Self-healing LLM process manager

Monitors the flash-moe infer process. If it crashes or becomes
unresponsive, the watchdog automatically restarts it with exponential
backoff. Exposes health state for server.py and the mind daemon.

Key design:
  - Singleton process: only ONE infer process at a time
  - Health probing: async HTTP GET /health every PROBE_INTERVAL seconds
  - Auto-restart: subprocess.Popen with proper signal handling
  - Exponential backoff: prevents restart storms (5s → 10s → 20s → 40s max)
  - State machine: STARTING → HEALTHY → UNHEALTHY → RESTARTING → STARTING
  - Dormant mode: GARY_SKIP_AUTOLAUNCH=1 disables auto-launch (used during
    first-run setup wizard before models are downloaded)
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable, Awaitable

import httpx

log = logging.getLogger("gary.watchdog")

# ── Configuration ────────────────────────────────────────────────────────────

GARY_LLM_HOST = os.getenv("GARY_LLM_HOST", "localhost")
GARY_LLM_PORT = int(os.getenv("GARY_LLM_PORT", "8088"))
LLM_URL  = f"http://{GARY_LLM_HOST}:{GARY_LLM_PORT}"

# Default: <repo-root>/flash-moe/infer  (vendored, compiled in-place)
# The repo root is two directories up from this file (gary/core/ → gary/ → repo-root)
_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

# Override with GARY_INFER_BIN env var
INFER_BIN = os.getenv(
    "GARY_INFER_BIN",
    os.path.join(_REPO_ROOT, "flash-moe", "infer"),
)


def _detect_hf_model(repo_id: str) -> str:
    """Find the latest snapshot of a HuggingFace model in the local cache."""
    import glob as _glob
    org, name = repo_id.split("/", 1)
    cache_dir = os.path.expanduser(
        f"~/.cache/huggingface/hub/models--{org}--{name}/snapshots"
    )
    snapshots = sorted(_glob.glob(os.path.join(cache_dir, "*")))
    # Return latest snapshot, or a placeholder path that will fail gracefully
    return snapshots[-1] if snapshots else os.path.join(cache_dir, "not-yet-downloaded")


# Override with GARY_INFER_MODEL env var, otherwise auto-detect from HF cache
INFER_MODEL = os.getenv(
    "GARY_INFER_MODEL",
    _detect_hf_model("mlx-community/Qwen3.5-35B-A3B-4bit"),
)

INFER_ARGS = [
    "--serve", str(GARY_LLM_PORT),
    "--model", INFER_MODEL,
    "--think-budget", "512",
    "--kv-seq", "2048",
]

# Dormant mode: when True, watchdog will NOT auto-launch the infer binary.
# Set GARY_SKIP_AUTOLAUNCH=1 during first-run setup (before models downloaded).
SKIP_AUTOLAUNCH = os.getenv("GARY_SKIP_AUTOLAUNCH", "0") == "1"

# Timing
PROBE_INTERVAL    = 10     # seconds between health probes
PROBE_TIMEOUT     = 120    # seconds (Flash-MoE prefills of 1k+ tokens can take 60s+ on SSD)
STARTUP_GRACE     = 30     # seconds to wait after starting before first probe
MAX_CONSECUTIVE_FAILS = 2  # number of failed probes before restarting

# Backoff
INITIAL_BACKOFF   = 5      # seconds
MAX_BACKOFF       = 40     # seconds
BACKOFF_MULTIPLIER = 2.0


class WatchdogState(Enum):
    STOPPED     = "stopped"
    STARTING    = "starting"
    HEALTHY     = "healthy"
    UNHEALTHY   = "unhealthy"
    RESTARTING  = "restarting"


@dataclass
class LLMWatchdog:
    """Manages the LLM infer process lifecycle."""

    state: WatchdogState = WatchdogState.STOPPED
    _process: Optional[subprocess.Popen] = field(default=None, repr=False)
    _consecutive_fails: int = 0
    _restart_count: int = 0
    _last_healthy: float = 0.0
    _backoff: float = INITIAL_BACKOFF
    _task: Optional[asyncio.Task] = field(default=None, repr=False)
    _on_state_change: Optional[Callable[[WatchdogState], Awaitable[None]]] = field(
        default=None, repr=False
    )

    @property
    def is_healthy(self) -> bool:
        return self.state == WatchdogState.HEALTHY

    @property
    def uptime(self) -> float:
        """Seconds since last healthy state, or 0 if not healthy."""
        if self._last_healthy and self.state == WatchdogState.HEALTHY:
            return time.monotonic() - self._last_healthy
        return 0.0

    @property
    def restart_count(self) -> int:
        return self._restart_count

    async def _set_state(self, new_state: WatchdogState):
        if new_state != self.state:
            old = self.state
            self.state = new_state
            log.info(f"[watchdog] {old.value} → {new_state.value}")
            if self._on_state_change:
                try:
                    await self._on_state_change(new_state)
                except Exception:
                    pass  # never let callback errors crash the watchdog

    def on_state_change(self, callback: Callable[[WatchdogState], Awaitable[None]]):
        """Register a callback for state transitions."""
        self._on_state_change = callback

    async def start(self):
        """Start the watchdog loop as an asyncio task."""
        if self._task and not self._task.done():
            log.warning("[watchdog] already running")
            return
        self._task = asyncio.create_task(self._run())
        log.info("[watchdog] started")

    async def stop(self):
        """Stop the watchdog and kill the LLM process."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._kill_process()
        await self._set_state(WatchdogState.STOPPED)

    def _is_process_alive(self) -> bool:
        """Check if the managed process is still running."""
        if self._process is None:
            return False
        return self._process.poll() is None

    def _kill_process(self):
        """Kill the current infer process if running."""
        if self._process and self._process.poll() is None:
            log.info(f"[watchdog] killing infer PID {self._process.pid}")
            try:
                os.killpg(os.getpgid(self._process.pid), signal.SIGTERM)
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(os.getpgid(self._process.pid), signal.SIGKILL)
                    self._process.wait(timeout=3)
            except (ProcessLookupError, OSError):
                pass  # already dead
        self._process = None

    async def _check_external_process(self) -> bool:
        """Check if an external infer process is already listening on the port."""
        try:
            async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
                resp = await client.get(f"{LLM_URL}/v1/models")
                return resp.status_code == 200
        except Exception:
            return False

    async def _probe_health(self) -> bool:
        """HTTP health probe. Returns True if LLM is responsive."""
        try:
            async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
                resp = await client.get(f"{LLM_URL}/v1/models")
                return resp.status_code == 200
        except Exception:
            return False

    def _start_process(self):
        """Launch the infer binary as a subprocess."""
        cmd = [INFER_BIN] + INFER_ARGS
        log.info(f"[watchdog] launching: {' '.join(cmd)}")

        # Redirect stdout/stderr to a log file relative to the project root
        # Start in a new process group so we can kill the whole tree
        _here = os.path.dirname(os.path.abspath(__file__))
        log_path = os.path.join(_here, "..", "logs", "llm_infer.log")
        log_path = os.path.normpath(log_path)
        os.makedirs(os.path.dirname(log_path), exist_ok=True)

        try:
            with open(log_path, "a") as log_file:
                self._process = subprocess.Popen(
                    cmd,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,  # no stdin — prevents REPL exit
                    preexec_fn=os.setsid,      # new process group
                    cwd=os.path.dirname(INFER_BIN),  # data files (out_35b/, vocab.bin, tokenizer.bin) live next to the binary
                )
            log.info(f"[watchdog] infer started PID={self._process.pid}")
        except OSError as e:
            log.warning(f"[watchdog] failed to launch infer ({e}) — is the binary compiled?")

    async def _run(self):
        """Main watchdog loop."""
        try:
            # Dormant mode: skip auto-launch (e.g. first-run setup wizard)
            if SKIP_AUTOLAUNCH:
                log.info("[watchdog] GARY_SKIP_AUTOLAUNCH=1 — dormant mode, not launching infer")
                await self._set_state(WatchdogState.STOPPED)
                return

            # First, check if an external process is already running
            if await self._check_external_process():
                log.info("[watchdog] external LLM already running and healthy")
                self._last_healthy = time.monotonic()
                await self._set_state(WatchdogState.HEALTHY)
            else:
                # No external process — launch our own
                await self._launch_and_wait()

            # Main monitoring loop
            while True:
                await asyncio.sleep(PROBE_INTERVAL)

                healthy = await self._probe_health()

                if healthy:
                    if self.state != WatchdogState.HEALTHY:
                        self._last_healthy = time.monotonic()
                        self._backoff = INITIAL_BACKOFF  # reset backoff
                        await self._set_state(WatchdogState.HEALTHY)
                    self._consecutive_fails = 0
                else:
                    self._consecutive_fails += 1
                    if self._consecutive_fails == 1:
                        log.warning("[watchdog] LLM probe failed (1st)")
                        await self._set_state(WatchdogState.UNHEALTHY)
                    elif self._consecutive_fails >= MAX_CONSECUTIVE_FAILS:
                        log.error(
                            f"[watchdog] {self._consecutive_fails} consecutive "
                            f"probe failures — restarting LLM"
                        )
                        await self._set_state(WatchdogState.RESTARTING)
                        self._kill_process()
                        await asyncio.sleep(self._backoff)
                        self._backoff = min(self._backoff * BACKOFF_MULTIPLIER, MAX_BACKOFF)
                        await self._launch_and_wait()
                        self._consecutive_fails = 0

        except asyncio.CancelledError:
            log.info("[watchdog] loop cancelled")
            raise

    async def _launch_and_wait(self):
        """Launch the infer process and wait for it to become healthy."""
        await self._set_state(WatchdogState.STARTING)
        self._start_process()
        self._restart_count += 1

        # Wait for startup grace period, probing periodically
        start = time.monotonic()
        while time.monotonic() - start < STARTUP_GRACE:
            await asyncio.sleep(2)
            if await self._probe_health():
                self._last_healthy = time.monotonic()
                self._backoff = INITIAL_BACKOFF
                await self._set_state(WatchdogState.HEALTHY)
                return

            # Check if process died during startup
            if not self._is_process_alive():
                log.error("[watchdog] infer process died during startup!")
                await self._set_state(WatchdogState.UNHEALTHY)
                return

        # If we got here, startup didn't succeed within grace period
        if await self._probe_health():
            self._last_healthy = time.monotonic()
            await self._set_state(WatchdogState.HEALTHY)
        else:
            log.warning("[watchdog] LLM did not become healthy within grace period")
            await self._set_state(WatchdogState.UNHEALTHY)


# Module-level singleton
watchdog = LLMWatchdog()
