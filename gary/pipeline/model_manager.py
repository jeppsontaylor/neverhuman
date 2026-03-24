"""
gary/pipeline/model_manager.py — Model detection, download, and status for the setup wizard

Handles:
  - Catalog of all supported models with HF repo IDs, sizes, and requirements
  - Detection of existing models from HuggingFace cache and local flash-moe binary
  - SSE-streaming download progress via huggingface_hub
  - flash-moe binary detection (GARY_INFER_BIN env var or ~/.neverhuman/flash-moe/infer)

Architecture note: ASR and TTS are pure HF downloads. The LLM has TWO components:
  1. flash-moe inference engine (compiled C binary) - handled by install.sh
  2. LLM model weights (HF download) - handled by this module
"""
from __future__ import annotations

import asyncio
import glob
import json
import logging
import os
import time
import sys
import shutil
import asyncio
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Optional

log = logging.getLogger("gary.model_manager")

# ── Model Catalog ─────────────────────────────────────────────────────────────

@dataclass
class ModelSpec:
    """Specification for a downloadable model."""
    id: str                    # Internal identifier
    category: str              # "asr" | "tts" | "llm"
    display_name: str          # Human-readable name
    hf_repo: str               # HuggingFace repository ID
    size_gb: float             # Approximate download size in GB
    description: str           # Short description for UI
    requires_token: bool = False       # True if HF token is needed
    vram_gb: Optional[float] = None    # Minimum VRAM/RAM required


MODEL_CATALOG: dict[str, list[ModelSpec]] = {
    "asr": [
        ModelSpec(
            id="qwen3-asr-0.6b",
            category="asr",
            display_name="Qwen3-ASR-0.6B",
            hf_repo="Qwen/Qwen3-ASR-0.6B",
            size_gb=0.6,
            description="Fast, accurate ASR optimised for Apple Silicon (MLX)",
        ),
    ],
    "tts": [
        ModelSpec(
            id="kokoro-82m",
            category="tts",
            display_name="Kokoro-82M",
            hf_repo="hexgrad/Kokoro-82M",
            size_gb=0.4,
            description="54 natural voices, ONNX, runs offline. State-of-the-art quality.",
        ),
    ],
    "llm": [
        ModelSpec(
            id="qwen3.5-35b-a3b-4bit",
            category="llm",
            display_name="Qwen3.5-35B-A3B (4-bit)",
            hf_repo="mlx-community/Qwen3.5-35B-A3B-4bit",
            size_gb=18.0,
            description="35B MoE brain, 4-bit quantised for Apple Silicon. Requires flash-moe engine.",
            vram_gb=6.0,
        ),
        ModelSpec(
            id="qwen3.5-7b-4bit",
            category="llm",
            display_name="Qwen3.5-7B (4-bit, lighter)",
            hf_repo="mlx-community/Qwen3.5-7B-4bit",
            size_gb=4.5,
            description="7B model, lighter on RAM. Good for 8GB unified memory Macs.",
            vram_gb=3.0,
        ),
    ],
}

# Default model selections
DEFAULT_ASR = "qwen3-asr-0.6b"
DEFAULT_TTS = "kokoro-82m"
DEFAULT_LLM = "qwen3.5-35b-a3b-4bit"


# ── Flash-moe binary detection ────────────────────────────────────────────────

INFER_BIN_DEFAULT = os.path.expanduser("~/.neverhuman/flash-moe/infer")

def detect_flash_moe() -> dict:
    """
    Check if the flash-moe infer binary exists and is executable.
    Returns a dict with 'ready', 'path', and 'message'.
    """
    infer_bin = os.getenv("GARY_INFER_BIN", INFER_BIN_DEFAULT)
    exists = os.path.isfile(infer_bin)
    executable = os.access(infer_bin, os.X_OK) if exists else False
    return {
        "ready": exists and executable,
        "path": infer_bin if exists else None,
        "message": (
            "Engine ready" if (exists and executable)
            else "Binary found but not executable" if exists
            else f"Not found at {infer_bin}. Run install.sh or set GARY_INFER_BIN."
        ),
    }


# ── HuggingFace cache detection ───────────────────────────────────────────────

def _hf_cache_dir(hf_repo: str) -> Path:
    """Return the HF cache directory for a given repo ID."""
    org, name = hf_repo.split("/", 1)
    return Path.home() / ".cache" / "huggingface" / "hub" / f"models--{org}--{name}"


def detect_model(spec: ModelSpec) -> dict:
    """
    Check if a model is already downloaded in the HF cache.
    Returns a dict with 'ready', 'path', and 'size_on_disk_gb'.
    """
    cache = _hf_cache_dir(spec.hf_repo)
    snapshots_dir = cache / "snapshots"
    if snapshots_dir.exists():
        snapshots = sorted(snapshots_dir.iterdir())
        if snapshots:
            path = snapshots[-1]
            # Estimate size on disk
            try:
                size_bytes = sum(
                    f.stat().st_size for f in path.rglob("*") if f.is_file()
                )
                size_gb = round(size_bytes / 1e9, 2)
            except (OSError, PermissionError):
                size_gb = None
            return {"ready": True, "path": str(path), "size_on_disk_gb": size_gb}
    return {"ready": False, "path": None, "size_on_disk_gb": 0}


def get_full_status() -> dict:
    """
    Return the complete status of all models and the flash-moe binary.
    Used by GET /api/setup/status.
    """
    status = {"flash_moe": detect_flash_moe(), "models": {}}
    for category, specs in MODEL_CATALOG.items():
        for spec in specs:
            detection = detect_model(spec)
            status["models"][spec.id] = {
                "id": spec.id,
                "category": spec.category,
                "display_name": spec.display_name,
                "hf_repo": spec.hf_repo,
                "size_gb": spec.size_gb,
                "description": spec.description,
                "requires_token": spec.requires_token,
                "vram_gb": spec.vram_gb,
                **detection,
            }
    return status


def get_catalog() -> dict:
    """Return the model catalog for use by GET /api/setup/models."""
    return {
        category: [
            {
                "id": s.id,
                "display_name": s.display_name,
                "hf_repo": s.hf_repo,
                "size_gb": s.size_gb,
                "description": s.description,
                "requires_token": s.requires_token,
                "vram_gb": s.vram_gb,
            }
            for s in specs
        ]
        for category, specs in MODEL_CATALOG.items()
    }


# ── Download Manager ──────────────────────────────────────────────────────────

# Active downloads keyed by model_id
@dataclass
class DownloadTask:
    process: asyncio.subprocess.Process
    last_event: dict = field(default_factory=dict)
    listeners: list[asyncio.Queue] = field(default_factory=list)
    done: bool = False

_active_downloads: dict[str, DownloadTask] = {}
_dashboard_task = None
_last_drawn_lines = 0

def _gradient_color(pct: float) -> str:
    """Returns an ANSI truecolor escape sequence scaling from Pink -> Yellow -> Green -> Cyan."""
    stops = [
        (0.0, (236, 72, 153)),   # Deep Pink
        (33.0, (234, 179, 8)),   # Brilliant Yellow
        (66.0, (34, 197, 94)),   # Emerald Green
        (100.0, (6, 182, 212))   # Cyan
    ]
    pct = max(0.0, min(100.0, float(pct)))
    
    for i in range(len(stops) - 1):
        if stops[i][0] <= pct <= stops[i+1][0]:
            start_p, start_c = stops[i]
            end_p, end_c = stops[i+1]
            break
    else:
        start_p, start_c = stops[-2]
        end_p, end_c = stops[-1]
        
    ratio = (pct - start_p) / (end_p - start_p) if end_p != start_p else 0
    r = int(start_c[0] + (end_c[0] - start_c[0]) * ratio)
    g = int(start_c[1] + (end_c[1] - start_c[1]) * ratio)
    b = int(start_c[2] + (end_c[2] - start_c[2]) * ratio)
    return f"\033[38;2;{r};{g};{b}m"

async def _terminal_dashboard_loop():
    """Prints a beautiful real-time 10fps progress dashboard of all active downloads to the terminal."""
    global _last_drawn_lines
    C_RESET = "\033[0m"
    C_DIM = "\033[90m"
    C_BOLD = "\033[1m"
    C_TITLE = "\033[38;2;139;92;246m" # Neon Purple
    
    icons = {"asr": "🎤", "tts": "🔊", "llm": "🧠"}
    
    try:
        while True:
            await asyncio.sleep(0.1) # 10 FPS
            
            if not _active_downloads:
                if _last_drawn_lines > 0:
                    sys.stdout.write(f"\033[{_last_drawn_lines}A\033[J")
                    sys.stdout.flush()
                    _last_drawn_lines = 0
                continue
            
            lines = []
            lines.append(f" {C_TITLE}{C_BOLD}╭─ NeverHuman Model Sync ──────────────────────────────────────────╮{C_RESET}")
            lines.append(f" {C_TITLE}│{C_RESET}                                                                  {C_TITLE}│{C_RESET}")
            
            for mid, task in list(_active_downloads.items()):
                spec = get_model_spec(mid)
                ev = task.last_event
                
                cat = next((c for c, specs in MODEL_CATALOG.items() if any(s.id == mid for s in specs)), "sys")
                icon = icons.get(cat, "📦")
                name = spec.display_name if spec else mid
                repo = spec.hf_repo if spec else "unknown"
                
                lines.append(f" {C_TITLE}│{C_RESET}  {icon} {C_BOLD}{name}{C_RESET} {C_DIM}({repo}){C_RESET}")
                
                if ev.get("type") == "progress":
                    pct = ev.get("pct", 0)
                    speed = ev.get("speed_mbps", 0.0)
                    color = _gradient_color(pct)
                    
                    bar_len = 35
                    filled = int((pct / 100) * bar_len)
                    empty = bar_len - filled
                    bar = "█" * filled + "░" * empty
                    
                    # Pad to ensure correct box framing
                    stat_str = f"{pct}% • {speed} MB/s"
                    pad = max(0, 60 - (bar_len + len(stat_str) + 7))
                    
                    lines.append(f" {C_TITLE}│{C_RESET}  {color}[{bar}] {stat_str}{C_RESET}{' ' * pad}{C_TITLE}│{C_RESET}")
                elif ev.get("type") == "checking":
                    lines.append(f" {C_TITLE}│{C_RESET}  {C_DIM}Starting connection...{' ' * 42}{C_TITLE}│{C_RESET}")
                elif ev.get("type") == "done":
                    color = _gradient_color(100)
                    lines.append(f" {C_TITLE}│{C_RESET}  {color}[{'█'*35}] 100% • Complete!{' ' * 11}{C_RESET}{C_TITLE}│{C_RESET}")
                elif ev.get("type") == "error":
                    lines.append(f" {C_TITLE}│{C_RESET}  \033[31m[ERROR] {ev.get('message', 'Failed')}{C_RESET}")
                else:
                    lines.append(f" {C_TITLE}│{C_RESET}  {C_DIM}Waiting...{' ' * 54}{C_TITLE}│{C_RESET}")
                
                lines.append(f" {C_TITLE}│{C_RESET}                                                                  {C_TITLE}│{C_RESET}")
                
            lines[-1] = f" {C_TITLE}╰──────────────────────────────────────────────────────────────────╯{C_RESET}"
            
            out = ""
            if _last_drawn_lines > 0:
                out += f"\033[{_last_drawn_lines}A\033[J" # Move up and clear
            
            out += "\n".join(lines) + "\n"
            sys.stdout.write(out)
            sys.stdout.flush()
            
            _last_drawn_lines = len(lines)
            
    except asyncio.CancelledError:
        if _last_drawn_lines > 0:
            sys.stdout.write(f"\033[{_last_drawn_lines}A\033[J")
            sys.stdout.flush()

async def _worker_monitor(model_id: str, task: DownloadTask):
    """Reads stdout from the background worker and broadcasts isolated JSON events to all UI listeners."""
    try:
        while True:
            line = await task.process.stdout.readline()
            if not line:
                break
            try:
                event = json.loads(line.decode().strip())
                task.last_event = event
                # notify all connected browser tabs
                for q in task.listeners:
                    q.put_nowait(event)
            except json.JSONDecodeError:
                continue
    finally:
        await task.process.wait()
        if not task.last_event.get("type") in ("done", "error"):
            # process died unexpectedly (or was killed by user)
            event = {"type": "error", "message": f"Worker exited with code {task.process.returncode}"}
            task.last_event = event
            for q in task.listeners:
                q.put_nowait(event)
        
        task.done = True
        
        # Keep in memory for 10s so late-reconnects see the final "done/error" state
        await asyncio.sleep(10)
        _active_downloads.pop(model_id, None)

async def start_download_bg(model_id: str, hf_token: Optional[str] = None) -> DownloadTask:
    """Spawns the isolated download worker and starts monitoring it."""
    spec = get_model_spec(model_id)
    if not spec:
        raise ValueError(f"Unknown model ID: {model_id}")
    
    # 1. Disk space verification
    free_gb = shutil.disk_usage(Path.home()).free / 1e9
    if free_gb < spec.size_gb:
        raise RuntimeError(f"Insufficient disk space. Need {spec.size_gb}GB, have {round(free_gb, 1)}GB.")
        
    worker_script = Path(__file__).parent / "download_worker.py"
    
    args = [sys.executable, str(worker_script), "--repo", spec.hf_repo, "--size-gb", str(spec.size_gb)]
    if hf_token:
        args.extend(["--token", hf_token])
        
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL
    )
    
    task = DownloadTask(process=process)
    _active_downloads[model_id] = task
    asyncio.create_task(_worker_monitor(model_id, task))
    
    # Lazily start terminal dashboard on first download
    global _dashboard_task
    if _dashboard_task is None:
        _dashboard_task = asyncio.create_task(_terminal_dashboard_loop())
        
    return task

def get_model_spec(model_id: str) -> Optional[ModelSpec]:
    """Look up a ModelSpec by its ID."""
    for specs in MODEL_CATALOG.values():
        for spec in specs:
            if spec.id == model_id:
                return spec
    return None

async def download_model_sse(
    model_id: str,
    hf_token: Optional[str] = None,
) -> AsyncIterator[str]:
    """
    Subscribes the UI to an active background download. 
    If none exists, safely spawns it. Resilient to browser refreshes.
    """
    spec = get_model_spec(model_id)
    if not spec:
        yield _sse({"type": "error", "message": f"Unknown model: {model_id}"})
        return

    # Check if natively already downloaded
    existing = detect_model(spec)
    if existing["ready"]:
        yield _sse({"type": "done", "path": existing["path"], "cached": True})
        return

    # Attach to existing global task or start new
    if model_id in _active_downloads:
        task = _active_downloads[model_id]
    else:
        try:
            task = await start_download_bg(model_id, hf_token)
        except Exception as e:
            yield _sse({"type": "error", "message": str(e)})
            return

    # Send last known state immediately so reconnecting UI updates instantly
    if task.last_event:
        yield _sse(task.last_event)
        if task.last_event.get("type") in ("done", "error"):
            return

    # Subscribe to live updates
    q = asyncio.Queue()
    task.listeners.append(q)
    try:
        while not task.done:
            try:
                event = await asyncio.wait_for(q.get(), timeout=15.0)
                yield _sse(event)
                if event.get("type") in ("done", "error"):
                    break
            except asyncio.TimeoutError:
                yield _sse({"type": "heartbeat"})
    except asyncio.CancelledError:
        # Browser disconnected gracefully! The background process keeps running safely.
        pass
    finally:
        if q in task.listeners:
            task.listeners.remove(q)

async def cancel_download(model_id: str):
    """Forcefully reap an ongoing download and prevent zombies."""
    if model_id in _active_downloads:
        task = _active_downloads[model_id]
        if task.process.returncode is None:
            task.process.kill()
        _active_downloads.pop(model_id, None)


def _sse(payload: dict) -> str:
    """Format a dict as an SSE data line."""
    return f"data: {json.dumps(payload)}\n\n"


# ── Convenience: all defaults ready? ─────────────────────────────────────────

def all_defaults_ready() -> bool:
    """
    Returns True if all default models are downloaded and flash-moe binary exists.
    Used by server.py to decide whether to redirect to /setup.
    """
    status = get_full_status()
    if not status["flash_moe"]["ready"]:
        return False
    for model_id in [DEFAULT_ASR, DEFAULT_TTS, DEFAULT_LLM]:
        if not status["models"].get(model_id, {}).get("ready", False):
            return False
    return True
