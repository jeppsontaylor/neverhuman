#!/usr/bin/env python3
"""
gary/pipeline/download_worker.py
Pure subprocess worker for downloading HuggingFace models safely.
Emits strictly JSON lines to stdout for the ASGI server to parse and stream to the UI.
Prevents ASGI thread locking, GIL contention, and drops.
"""
import json
import sys
import time
import argparse
import traceback
import threading

def emit(payload: dict):
    """Prints a JSON payload and forces a flush to the pipe."""
    print(json.dumps(payload))
    sys.stdout.flush()

try:
    from huggingface_hub import snapshot_download
    from tqdm import tqdm as _tqdm_base
except ImportError:
    emit({"type": "error", "message": "huggingface_hub not installed in worker env."})
    sys.exit(1)

_tqdm_lock = threading.Lock()

class SSETqdm(_tqdm_base):
    """
    Overrides tqdm to suppress terminal bars and instead emit JSON logs.
    Handles concurrent huggingface_hub thread calls safely via locks.
    """
    _cumulative_bytes = 0
    _cumulative_total = 0
    _last_time = time.monotonic()
    _last_bytes = 0
    _estimated_gbs = 0.0

    def __init__(self, *args, **kwargs):
        kwargs['disable'] = False
        kwargs.pop('name', None) # Filter HuggingFace internal param
        super().__init__(*args, **kwargs)
        with _tqdm_lock:
            if self.total:
                SSETqdm._cumulative_total += self.total

    def update(self, n=1):
        super().update(n)
        with _tqdm_lock:
            SSETqdm._cumulative_bytes += n
            now = time.monotonic()
            dt = now - SSETqdm._last_time
            
            # Throttle updates to ~3 times a second to prevent flooding the SSE loop
            if dt < 0.3 and SSETqdm._cumulative_bytes < SSETqdm._cumulative_total:
                return
                
            chunk = SSETqdm._cumulative_bytes - SSETqdm._last_bytes
            speed_bps = chunk / dt if dt > 0.5 else 0
            speed_mbps = round(speed_bps / 1e6, 2)
            
            total_est = max(SSETqdm._cumulative_total, int(SSETqdm._estimated_gbs * 1e9))
            pct = min(99, round(SSETqdm._cumulative_bytes / total_est * 100)) if total_est > 0 else 0
            remaining = total_est - SSETqdm._cumulative_bytes
            eta_s = int(remaining / speed_bps) if speed_bps > 0 else None
            
            SSETqdm._last_bytes = SSETqdm._cumulative_bytes
            SSETqdm._last_time = now
            
            emit({
                "type": "progress",
                "pct": pct,
                "speed_mbps": speed_mbps,
                "eta_s": eta_s,
                "bytes_done": SSETqdm._cumulative_bytes,
                "bytes_total": total_est,
            })

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--token", default=None)
    parser.add_argument("--size-gb", type=float, default=0.0)
    args = parser.parse_args()

    # Disable terminal tqdm printing globally since we subclassed it
    SSETqdm._estimated_gbs = args.size_gb

    emit({"type": "checking", "message": f"Initiating concurrent download blocks..."})
    
    try:
        path = snapshot_download(
            repo_id=args.repo,
            token=args.token if args.token else None,
            tqdm_class=SSETqdm
        )
        # One last progress burst for 100%
        emit({
            "type": "progress",
            "pct": 100,
            "speed_mbps": 0.0,
            "eta_s": 0,
            "bytes_done": SSETqdm._cumulative_bytes,
            "bytes_total": SSETqdm._cumulative_bytes,
        })
        emit({"type": "done", "path": path, "cached": False})
    except Exception as e:
        emit({"type": "error", "message": str(e)})
        sys.exit(1)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        emit({"type": "error", "message": f"Fatal worker crash: {str(e)}"})
        sys.exit(1)
