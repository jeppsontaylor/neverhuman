"""
benchmarks/latency.py — GARY v2 Latency Benchmarking Harness

Measures the critical latency components of the voice pipeline:
  1. VAD latency: time to detect speech onset
  2. ASR latency: time to transcribe utterance
  3. TTFT (Time to First Token): LLM response start
  4. TTS first-sentence: time from LLM sentence → WAV bytes
  5. Full round-trip: utterance → first audio byte

Usage:
    # From GARY/ directory with .venv active
    python benchmarks/latency.py

    # Or specific benchmarks:
    python benchmarks/latency.py --vad-only
    python benchmarks/latency.py --full-pipeline
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import os
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path

import numpy as np

# Add GARY root
sys.path.insert(0, str(Path(__file__).parent.parent))

log = logging.getLogger("gary.bench")
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

_SR = 16_000


@dataclass
class BenchResult:
    name: str
    runs: int = 0
    total_ms: float = 0.0
    min_ms: float = float("inf")
    max_ms: float = 0.0

    def add(self, ms: float):
        self.runs += 1
        self.total_ms += ms
        self.min_ms = min(self.min_ms, ms)
        self.max_ms = max(self.max_ms, ms)

    @property
    def avg_ms(self) -> float:
        return self.total_ms / max(1, self.runs)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "runs": self.runs,
            "avg_ms": round(self.avg_ms, 2),
            "min_ms": round(self.min_ms, 2) if self.min_ms != float("inf") else None,
            "max_ms": round(self.max_ms, 2),
        }

    def __str__(self):
        if self.runs == 0:
            return f"{self.name}: no runs"
        return (
            f"{self.name}: avg={self.avg_ms:.1f}ms "
            f"min={self.min_ms:.1f}ms max={self.max_ms:.1f}ms "
            f"({self.runs} runs)"
        )


def generate_speech_chunk(duration_sec: float = 0.16) -> np.ndarray:
    """Generate a synthetic speech-like audio chunk for benchmarking."""
    n = int(duration_sec * _SR)
    t = np.arange(n) / _SR
    # Mix of speech-band frequencies
    audio = (
        0.25 * np.sin(2 * np.pi * 150 * t) +
        0.15 * np.sin(2 * np.pi * 500 * t) +
        0.10 * np.sin(2 * np.pi * 1500 * t) +
        0.05 * np.sin(2 * np.pi * 2500 * t)
    ).astype(np.float32)
    return audio


def generate_silence_chunk(duration_sec: float = 0.16) -> np.ndarray:
    return np.zeros(int(duration_sec * _SR), dtype=np.float32)


# ── VAD Benchmark ─────────────────────────────────────────────────────────────

def bench_vad_spectral(n_iters: int = 100) -> BenchResult:
    """Benchmark the original spectral SpeechDetector."""
    from pipeline.vad import SpeechDetector
    detector = SpeechDetector()
    chunk = generate_speech_chunk(0.16)  # 160ms

    result = BenchResult("VAD (spectral)")
    for _ in range(n_iters):
        t0 = time.perf_counter()
        detector.probability(chunk)
        ms = (time.perf_counter() - t0) * 1000
        result.add(ms)
    return result


def bench_vad_silero(n_iters: int = 100) -> BenchResult:
    """Benchmark the Silero VAD ONNX model."""
    try:
        import onnxruntime
    except ImportError:
        log.warning("onnxruntime not installed, skipping Silero VAD benchmark")
        return BenchResult("VAD (Silero) — skipped")

    from pipeline.silero_vad import SileroVAD
    vad = SileroVAD()
    chunk = generate_speech_chunk(0.16)

    # Warmup
    vad.probability(chunk)
    vad.probability(chunk)

    result = BenchResult("VAD (Silero)")
    for _ in range(n_iters):
        t0 = time.perf_counter()
        vad.probability(chunk)
        ms = (time.perf_counter() - t0) * 1000
        result.add(ms)
    return result


# ── Context Hints Benchmark ───────────────────────────────────────────────────

def bench_context_hints(n_iters: int = 1000) -> BenchResult:
    """Benchmark context hint string generation."""
    from pipeline.context_hints import ContextHints
    hints = ContextHints()
    hints.add_user_terms([f"UserTerm_{i}" for i in range(50)])
    for i in range(20):
        hints.add_session_term(f"SessionTerm_{i}")

    result = BenchResult("Context hints build")
    for _ in range(n_iters):
        t0 = time.perf_counter()
        hints.get_context_string()
        ms = (time.perf_counter() - t0) * 1000
        result.add(ms)
    return result


# ── Affect Engine Benchmark ───────────────────────────────────────────────────

def bench_affect_deltas(n_iters: int = 10000) -> BenchResult:
    """Benchmark fast-path affect delta application (must be < 0.1ms)."""
    from core.affect_types import AffectVector, AFFECT_DELTAS
    av = AffectVector()

    result = BenchResult("Affect delta apply")
    for i in range(n_iters):
        event_name = list(AFFECT_DELTAS.keys())[i % len(AFFECT_DELTAS)]
        t0 = time.perf_counter()
        av.apply_delta(AFFECT_DELTAS[event_name])
        ms = (time.perf_counter() - t0) * 1000
        result.add(ms)
    return result


def bench_affect_decay(n_iters: int = 10000) -> BenchResult:
    """Benchmark EMA decay (must be < 0.05ms)."""
    from core.affect_types import AffectVector
    av = AffectVector(loneliness=0.8, excitement=0.9, anxiety=0.5)

    result = BenchResult("Affect decay")
    for _ in range(n_iters):
        t0 = time.perf_counter()
        av.decay(now=time.monotonic() + 0.2)
        ms = (time.perf_counter() - t0) * 1000
        result.add(ms)
    return result


# ── Policy Benchmark ──────────────────────────────────────────────────────────

def bench_policy_curves(n_iters: int = 10000) -> BenchResult:
    """Benchmark BehaviorCurves computation."""
    from core.policies import BehaviorCurves

    result = BenchResult("Policy curves")
    for i in range(n_iters):
        h = (i % 100) / 100.0
        t0 = time.perf_counter()
        bc = BehaviorCurves(humanity=h)
        _ = bc.to_dict()
        ms = (time.perf_counter() - t0) * 1000
        result.add(ms)
    return result


# ── Main ──────────────────────────────────────────────────────────────────────

def run_all():
    print("\n" + "=" * 60)
    print("  GARY v2 · Latency Benchmark Suite")
    print("=" * 60 + "\n")

    results = []

    # VAD
    print("▸ VAD (spectral)...")
    r = bench_vad_spectral()
    print(f"  {r}")
    results.append(r)

    print("▸ VAD (Silero ONNX)...")
    r = bench_vad_silero()
    print(f"  {r}")
    results.append(r)

    # Context hints
    print("▸ Context hints...")
    r = bench_context_hints()
    print(f"  {r}")
    results.append(r)

    # Affect engine
    print("▸ Affect delta apply...")
    r = bench_affect_deltas()
    print(f"  {r}")
    results.append(r)

    print("▸ Affect decay...")
    r = bench_affect_decay()
    print(f"  {r}")
    results.append(r)

    # Policy curves
    print("▸ Policy curves...")
    r = bench_policy_curves()
    print(f"  {r}")
    results.append(r)

    # Summary
    print("\n" + "─" * 60)
    print("Summary:")
    print("─" * 60)
    for r in results:
        if r.runs > 0:
            status = "✓" if r.avg_ms < 10.0 else "⚠" if r.avg_ms < 50.0 else "✗"
            print(f"  {status} {r}")

    # Budget check
    print("\n" + "─" * 60)
    print("Latency budget (target: <400ms utterance → first audio):")
    print("─" * 60)
    for r in results:
        if r.runs > 0 and "skipped" not in r.name:
            print(f"  {r.name}: {r.avg_ms:.1f}ms")
    print(f"  ASR (Qwen3-ASR): ~100-200ms (not benchmarked — requires model)")
    print(f"  LLM TTFT: ~50-200ms (not benchmarked — requires flash-moe)")
    print(f"  TTS (Kokoro): ~30-80ms (not benchmarked — requires model)")
    print()

    # Save results
    out = Path(__file__).parent / "results.json"
    results_dicts = [r.to_dict() for r in results if r.runs > 0]
    out.write_text(json.dumps(results_dicts, indent=2))
    print(f"Results saved to {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GARY Latency Benchmarks")
    parser.add_argument("--vad-only", action="store_true")
    args = parser.parse_args()

    if args.vad_only:
        r = bench_vad_spectral()
        print(r)
        r = bench_vad_silero()
        print(r)
    else:
        run_all()
