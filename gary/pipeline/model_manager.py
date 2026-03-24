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
_active_downloads: dict[str, asyncio.Task] = {}


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
    Async generator yielding SSE-formatted progress events for a model download.

    Events:
      data: {"type": "progress", "pct": 45, "speed_mbps": 12.4, "eta_s": 42, "bytes": 1234}
      data: {"type": "checking", "message": "Verifying..."}
      data: {"type": "done", "path": "/path/to/snapshot"}
      data: {"type": "error", "message": "..."}
    """
    spec = get_model_spec(model_id)
    if not spec:
        yield _sse({"type": "error", "message": f"Unknown model: {model_id}"})
        return

    # Check if already downloaded
    existing = detect_model(spec)
    if existing["ready"]:
        yield _sse({"type": "done", "path": existing["path"], "cached": True})
        return

    try:
        from huggingface_hub import snapshot_download
        from huggingface_hub import HfApi
        import threading

        # Use a thread + queue to bridge sync huggingface_hub callbacks with async
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        total_bytes = int(spec.size_gb * 1e9)
        start_time = time.monotonic()
        last_bytes = [0]
        last_time = [start_time]

        def progress_callback(downloaded: int, total: int):
            now = time.monotonic()
            elapsed = now - start_time
            chunk = downloaded - last_bytes[0]
            dt = now - last_time[0]
            speed_bps = chunk / dt if dt > 0.5 else 0
            speed_mbps = round(speed_bps / 1e6, 2)
            pct = round(downloaded / total * 100) if total > 0 else 0
            remaining = total - downloaded
            eta_s = int(remaining / speed_bps) if speed_bps > 0 else None
            last_bytes[0] = downloaded
            last_time[0] = now
            event = {
                "type": "progress",
                "pct": pct,
                "speed_mbps": speed_mbps,
                "eta_s": eta_s,
                "bytes_done": downloaded,
                "bytes_total": total,
            }
            asyncio.run_coroutine_threadsafe(queue.put(event), loop)

        def _download():
            try:
                # Custom tqdm class that bridges huggingface_hub progress → async SSE queue
                from tqdm import tqdm as _tqdm_base

                class _SSETqdm(_tqdm_base):
                    """Tqdm subclass that pushes progress to an asyncio queue for SSE streaming."""
                    _cumulative_bytes = 0
                    _cumulative_total = 0

                    def __init__(self, *args, **kwargs):
                        kwargs['disable'] = False
                        super().__init__(*args, **kwargs)
                        if self.total:
                            _SSETqdm._cumulative_total += self.total

                    def update(self, n=1):
                        super().update(n)
                        _SSETqdm._cumulative_bytes += n
                        now = time.monotonic()
                        dt = now - last_time[0]
                        if dt < 0.3 and _SSETqdm._cumulative_bytes < _SSETqdm._cumulative_total:
                            return  # throttle updates
                        chunk = _SSETqdm._cumulative_bytes - last_bytes[0]
                        speed_bps = chunk / dt if dt > 0.5 else 0
                        speed_mbps = round(speed_bps / 1e6, 2)
                        total_est = max(_SSETqdm._cumulative_total, int(spec.size_gb * 1e9))
                        pct = min(99, round(_SSETqdm._cumulative_bytes / total_est * 100)) if total_est > 0 else 0
                        remaining = total_est - _SSETqdm._cumulative_bytes
                        eta_s = int(remaining / speed_bps) if speed_bps > 0 else None
                        last_bytes[0] = _SSETqdm._cumulative_bytes
                        last_time[0] = now
                        event = {
                            "type": "progress",
                            "pct": pct,
                            "speed_mbps": speed_mbps,
                            "eta_s": eta_s,
                            "bytes_done": _SSETqdm._cumulative_bytes,
                            "bytes_total": total_est,
                        }
                        asyncio.run_coroutine_threadsafe(queue.put(event), loop)

                _SSETqdm._cumulative_bytes = 0
                _SSETqdm._cumulative_total = 0

                path = snapshot_download(
                    repo_id=spec.hf_repo,
                    token=hf_token or os.getenv("HF_TOKEN"),
                    tqdm_class=_SSETqdm,
                )
                asyncio.run_coroutine_threadsafe(
                    queue.put({"type": "done", "path": path}), loop
                )
            except Exception as exc:
                asyncio.run_coroutine_threadsafe(
                    queue.put({"type": "error", "message": str(exc)}), loop
                )

        yield _sse({"type": "checking", "message": f"Starting download: {spec.display_name}"})

        # Start download in background thread
        thread = threading.Thread(target=_download, daemon=True)
        thread.start()

        # Yield events as they arrive
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                yield _sse({"type": "heartbeat"})
                continue

            yield _sse(event)

            if event["type"] in ("done", "error"):
                break

    except ImportError:
        yield _sse({
            "type": "error",
            "message": "huggingface_hub not installed. Run: pip install huggingface_hub",
        })
    except Exception as exc:
        log.exception(f"Download error for {model_id}: {exc}")
        yield _sse({"type": "error", "message": str(exc)})


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
