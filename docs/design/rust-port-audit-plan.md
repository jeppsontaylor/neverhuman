# Rust-First Port Audit & Migration Plan

> Date: 2026-03-29  
> Goal: move GARY to a **Rust-first runtime** wherever external Python-only ML dependencies are not required.

## 1) Current audit snapshot

Repository audit (runtime tree `gary/**/*.py`):

- **Python files**: 91
- **Total Python LOC**: 16,415
- Largest runtime modules include:
  - `server.py` (1,720 LOC)
  - `pipeline/turn_supervisor.py` (822 LOC)
  - `core/mind.py` (488 LOC)
  - `pipeline/model_manager.py` (464 LOC)
  - `pipeline/vad.py` (403 LOC)
  - `core/session_logger.py` (373 LOC)
  - `pipeline/context_pack.py` (300 LOC)
  - `pipeline/turn_classifier.py` (284 LOC)
  - `memory/retrieval.py` (283 LOC)

## 2) Rust-first boundary policy

Use Python **only** where there is a hard external dependency that is currently Python-native:

1. **ML model wrappers**
   - MLX / ONNX / model SDK adapters where Rust parity is not practical today.
2. **FastAPI-specific surfaces**
   - until those endpoints are replaced by Rust HTTP services.

Everything else should be treated as Rust-port candidates:
- orchestration logic
- policy engines
- classification
- queue/scheduling primitives
- logging and metrics pipelines
- retrieval scoring logic (non-DB driver-specific)

## 3) File-by-file port classification

## A. Immediate Rust ports (high ROI, low dependency risk)

1. `pipeline/turn_classifier.py`
   - Deterministic regex/rules; no Python-only ML dependency.
   - Port to Rust crate `turn_classifier_rs`.
2. `pipeline/turn_policy.py`
   - Already deterministic and small; best candidate for full Rust ownership.
3. `pipeline/tempo_controller.py`
   - Replace Python fallback with Rust FFI or persistent Rust sidecar call path.
4. `core/resource_arbiter.py`, `core/rumination_governor.py`, `core/eval_metrics.py`
   - Pure policy/math/state logic.

## B. Near-term ports (medium complexity)

1. `core/session_logger.py`, `core/log_writer.py`
   - Port to Rust async logging service for lower overhead and stronger concurrency.
2. `memory/spool.py`
   - Port to Rust append-only spool with fsync + replay.
3. `pipeline/context_pack.py`
   - Port composition logic; keep DB calls behind a boundary.

## C. Keep Python for now (external dependency anchored)

1. `pipeline/asr.py`, `pipeline/tts.py`, `pipeline/silero_vad.py`
2. `pipeline/llm.py` (transport can be ported later; currently tied to existing async/httpx usage and call sites)
3. `apps/mindd/*` where Python model wrappers are still required.

## 4) Target architecture after migration

- **Rust core process** owns:
  - WebSocket turn loop
  - turn classifier + policy + tempo
  - scheduling, arbitration, queues
  - logging/spool/metrics
- **Python model adapters** become narrow workers:
  - ASR worker
  - TTS worker
  - optional mind model worker
- Clear IPC contracts:
  - gRPC or unix socket JSON-RPC
  - strict request/response schemas

## 5) Migration phases

## Phase R0 — Governance & safety rails (1 week)

- Freeze new Python additions in core runtime modules.
- Require every new runtime logic module to be Rust unless justified.
- Add CI gate:
  - reject net-new Python files under `gary/pipeline`, `gary/core`, `gary/memory` unless allowlisted.

**Exit criteria:**
- CI policy active; no unauthorized new Python runtime files.

## Phase R1 — Deterministic policy stack port (2–3 weeks)

Port to Rust:
- turn classifier
- tempo controller
- turn policy

Python usage:
- thin bindings only.

**Exit criteria:**
- parity tests passing against existing Python fixtures.
- p95 classification latency no worse than baseline.

## Phase R2 — Logging/spool/metrics core port (2–3 weeks)

Port to Rust:
- session logging writer
- persistent logs
- spool/replay engine

**Exit criteria:**
- replay determinism tests pass.
- no data-loss regressions under crash tests.

## Phase R3 — Server loop split (3–5 weeks)

Move realtime orchestration out of `server.py` into Rust service.
Keep Python only for model worker calls.

**Exit criteria:**
- full WS turn flow parity.
- barge-in/TTFMA metrics equal or improved.

## Phase R4 — Retrieval/context and mind-policy cores (4+ weeks)

Port deterministic retrieval scoring/context composition/policy governors.

**Exit criteria:**
- retrieval relevance parity benchmarks.
- no latency regression at p95/p99.

## 6) Testing plan for Rust migration

For each ported module, enforce:

1. **Golden parity tests**
   - same input fixtures run against Python legacy and Rust module.
   - exact or bounded-epsilon output parity.

2. **Property tests**
   - fuzz inputs for classifier/policy invariants.

3. **Concurrency tests**
   - multi-thread race/load tests for logger/spool and scheduling code.

4. **Performance regression tests**
   - microbenchmarks on hot-path functions.
   - fail CI if latency/regression exceeds threshold.

5. **Memory bounds tests**
   - long-run tests to ensure stable RSS and bounded caches.

## 7) Immediate next implementation steps (concrete)

1. Create Rust `turn_classifier_rs` crate with fixture parity test vectors copied from `testing/test_turn_classifier.py`.
2. Replace Python `build_turn_policy` implementation with Rust-backed call path (Python wrapper only).
3. Add CI check that blocks net-new Python files in `pipeline/core/memory` unless explicit allowlist override.

## 8) Success criteria

A phase is considered complete only if:
- behavior parity tests pass,
- performance is equal or better,
- memory bounds are stable,
- Python surface area is reduced (measurable file+LOC delta),
- and fallback/rollback path is documented.

## 9) Migration progress updates

- ✅ **Phase R1 / Item 1 (started):** Rust `turn_classifier` crate created at `gary/rust/turn_classifier` with CLI and unit tests.
- ✅ Python `pipeline/turn_classifier.py` now supports a Rust-first execution path via `GARY_TURN_CLASSIFIER_BIN`.
- ✅ Added tests validating Rust binary path wiring in `testing/test_turn_classifier.py`.
- ✅ Extended Rust `turn_classifier` to support v2 three-axis classification (depth + intent + reasoning) and wired Python `classify_turn_v2` to the Rust binary first.
- ✅ Started porting arbitration core: added Rust `resource_arbiter` crate and wired `core/resource_arbiter.py` to use Rust-first execution via `GARY_RESOURCE_ARBITER_BIN`.
