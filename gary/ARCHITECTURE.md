# NeverHuman / GARY — Architecture Reference

> **Version**: 4.0 · 2026-03-24
> **Purpose**: Definitive reference for every subsystem, data flow, design decision, and integration point. Intended to be handed to any engineer or AI agent who needs to understand, critique, modify, or extend GARY.
> **Informed by**: 8 independent architectural reviews + all notes in `docs/internal/research_notes/` + v4.0 implementation

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Design Principles](#2-design-principles)
2b. [Context Pack v2](#2b-context-pack-v2)
3. [Architecture: Four-Process Cognitive OS](#3-architecture-four-process-cognitive-os)
4. [Process 1: Reflex Core](#4-process-1-reflex-core)
5. [Process 2: Memory Spine](#5-process-2-memory-spine)
6. [Process 3: Mind Daemon](#6-process-3-mind-daemon)
7. [Process 4: Learning Lab](#7-process-4-learning-lab)
8. [The Humanity Slider](#8-the-humanity-slider)
9. [Emotional Substrate](#9-emotional-substrate)
10. [Cognitive Flywheel](#10-cognitive-flywheel)
11. [Initiative & Shadow Mode](#11-initiative--shadow-mode)
12. [Database Schema (21 Tables)](#12-database-schema-21-tables)
13. [Retrieval System](#13-retrieval-system)
14. [Data Flows](#14-data-flows)
15. [WebSocket Protocol](#15-websocket-protocol)
16. [Storage & Docker](#16-storage--docker)
17. [Performance & Memory Protection](#17-performance--memory-protection)
18. [Evaluation Harness](#18-evaluation-harness)
19. [Tech Stack](#19-tech-stack)
20. [Configuration Reference](#20-configuration-reference)
21. [Security & TLS](#21-security--tls)
22. [Known Limitations](#22-known-limitations)
23. [Extension Guide](#23-extension-guide)
24. [Build Phases](#24-build-phases)
25. [Session Logging & Persistent Logs](#25-session-logging--persistent-logs)
26. [Turn Classifier](#26-turn-classifier)
27. [Structured Mind Pulses (JSON v1)](#27-structured-mind-pulses-json-v1)

---

## 1. System Overview

GARY is a **real-time, voice-first AI assistant** that runs entirely on a local Mac with Apple Silicon. It goes beyond a simple chatbot by implementing a **cognitive operating system** with four isolated processes: a sacred reflex loop for instant voice interaction, a durable memory spine for perfect recall, a background mind daemon for reflection and emotional processing, and an offline learning lab for continuous self-improvement.

```
┌─────────────────────────────────────────────────────────────┐
│             Browser SPA (index.html, ~3090 lines)           │
│  · Click-to-activate overlay (user gesture unlocks audio)    │
│  · AudioWorklet mic capture 16kHz (80ms chunks, v3.1)        │
│  · WebSocket binary frames (Float32 PCM)                    │
│  · Markdown-rendered chat bubbles + streaming cursor         │
│  · TTS WAV playback queue + animated waveform orb            │
│  · Think tokens → bright green (#00ff88) in log panel        │
│  · Mind tokens → hot pink (#ff69b4) in log panel             │
│  · Humanity slider · Voice selector · Mind Inspector panel   │
└──────────────────────────┬──────────────────────────────────┘
                           │  wss://localhost:7861/ws/gary
                           │  (binary: PCM up, WAV down)
                           │  (JSON: events both ways)
┌──────────────────────────▼──────────────────────────────────┐
│  PROCESS 1: REFLEX CORE  (server.py ~1465L, FastAPI+uvicorn) │
│  · Spectral VAD + RMS (pure numpy, <0.5ms/chunk)             │
│  · ASR (Qwen3-ASR-0.6B, MLX, lazy load)                     │
│  · TTS (Kokoro-82M, ONNX, parallel queue)                    │
│  · Turn Classifier (SNAP/LAYERED/DEEP, <1ms; §26)            │
│  · Filler audio (instant WAV before LLM; §4.11)              │
│  · Pre-baked greeting WAV (instant on connect; §4.13)         │
│  · Turn-epoch audio envelopes (audio_start/stop_audio; §4.14)│
│  · Context Pack v2 (DB-backed slots; §2b)                    │
│  · Barge-in detector (spectral + RMS; see §4.10)             │
│  · Local Append Spool (fsync'd JSONL, crash-safe; see §4.9)  │
│  · llm_gate timeout (3×2s + force mind yield; §4.12)         │
│  · is_speaking failsafe (30s hard cap; §4.16)                │
│  · Session Logger (dual-tier JSONL; §25)                     │
│  · Persistent Log Writer (3 log files; §25)                  │
│  → Latency budget: <400ms speech-end to first audio          │
└──────────────────────────┬──────────────────────────────────┘
                           │  http://localhost:8088/v1/chat/completions
                           │  (OpenAI-compatible SSE streaming; `LLM_PORT` / `pipeline/llm.py`)
┌──────────────────────────▼──────────────────────────────────┐
│  mac_flash_moe · metal_infer/infer                          │
│  Qwen3.5-35B-A3B-4bit (MoE, SSD-streamed experts)          │
│  Apple Metal acceleration · ~3-4GB resident (18GB on SSD)    │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  PROCESS 2: MEMORY SPINE  (Docker: Postgres 16 + pgvector)  │
│  20 tables · Schema v6.0 · HNSW indexes · LISTEN/NOTIFY     │
│  Named Docker volume · Append-only event ledger              │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  PROCESS 3: MIND DAEMON  (apps/mindd/, low priority)        │
│  Sidecar LLM (0.8-4B, MLX) · FastAPI on port 7863           │
│  /pulse + /health · Client-disconnect cancellation           │
│  Raced pulse (voice preempts background, asyncio.wait)       │
│  9 cognitive lanes (core/prompts/*.txt)                      │
│  Question Ledger · Commitment Tracker · Eval Harness         │
│  Rumination Governor · Appraisal · Safety Governor           │
│  Experience Distillery → training_buffer                     │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  PROCESS 4: LEARNING LAB  (apps/trainerd/, lowest priority) │
│  LoRA fine-tune (MLX-LM) · Counterfactual Rehearsal          │
│  Eval gates · Rollback · Biometric consent isolation         │
└─────────────────────────────────────────────────────────────┘
```

**Single inviolable rule**: When the user speaks, everything else yields instantly.

---

## 2. Design Principles

1. **Never block the voice loop.** The reflex path is sacred — sub-400ms from utterance end to first audio.
2. **Store everything, retrieve selectively.** Postgres is the immutable truth. Context packs are curated, not dumps.
3. **One slider controls the human element.** `humanity ∈ [0.0, 1.0]` — from pure tool to full inner life.
4. **Emotion shapes behavior, never truth.** Affect changes initiative/pacing/style but never epistemic status.
5. **Shorter is better.** Concise responses are rewarded. Verbosity wastes the user's time and TTS budget.
6. **Experience makes it smarter.** Request→outcome training pairs continuously improve the sidecar LLM.
7. **Docker for state, host for compute.** Postgres in Docker w/ named volume. ML inference on host Metal.
8. **Protect memory.** Flash-MoE SSD streaming, lazy ASR/TTS load/unload, `madvise`, bounded KV cache. Never exceed 10GB resident.

---

### 2b. Context Pack v2

The context compiler (`pipeline/context_pack.py`, v2) prepends a system message with structured retrieval slots:

| Slot | Source | DB-backed? |
|------|--------|------------|
| `LAST_USER` | Conversation history | No |
| `PRIOR_ASSISTANT` | Conversation history | No |
| `TURNS` | History count | No |
| `ACTIVE_LOOPS` | `open_loops` table (status='open') | ✅ Yes |
| `TOP_CLAIMS` | `claims` table (top-3 by confidence) | ✅ Yes |
| `TOP_QUESTION` | `questions` table (highest salience) | ✅ Yes |
| `AFFECT` | RAM `AffectVector` → summary string | Partial |

When `GARY_CONTEXT_PACK=1`, `pack_history_limit()` reduces `MAX_HISTORY_TURNS` from 20 → 10 to avoid token duplication. Retrieval audit logged to `retrieval_log` table. DB failures gracefully fall back to v1 (history-only slots).

---

## 3. Architecture: Four-Process Cognitive OS

The system uses **hard OS-level process isolation** to guarantee that background cognition never steals latency from the voice loop.

| Process | Priority | Responsibility | Failure Mode |
|---------|----------|----------------|-------------|
| **Reflex Core** | Highest | Voice pipeline, user interaction | Fatal — system unusable |
| **Memory Spine** | High | Durable storage, event ledger | Degraded — spool buffers locally |
| **Mind Daemon** | Low | Reflection, affect, initiative | Silent — user doesn't notice |
| **Learning Lab** | Lowest | Fine-tuning, eval, curation | Silent — deferred indefinitely |

**Circuit Breaker**: If TTFT (time to first token) or barge-in latency drifts above 300ms, a circuit breaker pauses ALL background work immediately.

---

## 4. Process 1: Reflex Core

### 4.1 Flash-MoE SSD Streaming Engine

The crown jewel. A 7680-line Objective-C/Metal inference engine for Qwen3.5-35B MoE:

- **SSD streaming via `pread()`**: Only K=8 experts loaded per token from 18GB packed weights on SSD. Never fully resident.
- **Parallel I/O**: 4 pthreads + GCD dispatch for concurrent expert reads into Metal buffers
- **Double-buffered expert data**: Set A for GPU compute, Set B for background I/O — overlaps SSD reads with GPU work
- **3 command buffers per layer**: CMD1 (attention) → CPU (RoPE/softmax) → CMD2 (routing + shared expert) → CPU (top-K + pread) → CMD3 (expert forwards + combine + residual)
- **`madvise(MADV_DONTNEED)`**: Pressure relief for non-expert weight pages
- **`--kv-seq 2048`**: KV cache capped at ~200MB
- **Effective memory**: ~3-4GB resident (vs. 20GB+ if fully loaded)
- **API**: OpenAI-compatible on `localhost:8088` (SSE streaming; default `LLM_PORT` in `core/llm_watchdog.py`)

**Status**: ✅ Production. Do not modify. GARY communicates via HTTP SSE.

### 4.2 Server (`server.py` — ~1465 lines)

FastAPI + uvicorn monolith on port 7861 (HTTPS). Single file, no blueprints.

#### HTTP Routes

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/` | Serves `static/index.html` |
| `GET` | `/static/*` | Static files (processor.js, etc.) |
| `GET` | `/health` | JSON: `{status, tts, llm_live}` |
| `GET` | `/api/voices` | Returns `{voices: [...], current: "am_adam"}` |
| `GET` | `/api/memory_status` | JSON: `{asr_loaded, tts_loaded, free_ram_gb, mem_warn}` |
| `GET` | `/api/logs/latest/{type}` | Download most recent session log (`condensed` or `detailed` JSONL) |
| `GET` | `/logs/api/{name}` | Raw log text by name (`conversation`, `mind_stream`, `websocket`) with byte offset for polling |
| `GET` | `/logs/` | Self-contained dark-themed HTML page for real-time log viewing |
| `WS` | `/ws/gary` | Full voice pipeline |

#### Per-Connection State

```python
history: list[dict]           # Sliding window (max 20; 10 when context pack enabled)
interrupt_event: asyncio.Event # Cancels in-flight LLM/TTS
current_task: asyncio.Task     # Wraps _generate_and_speak()
is_speaking: bool              # True while browser is playing audio (set False by tts_finished)
is_speaking_since: float       # v3.2: monotonic timestamp when is_speaking was last set True
IS_SPEAKING_TIMEOUT: float     # v3.2: 30.0s hard cap — failsafe resets is_speaking if stuck
turn_epoch: int                # Monotonic counter for audio envelope protocol (v3.1)
current_turn_mode: TurnMode    # v4: SNAP/LAYERED/DEEP from turn_classifier (default: LAYERED)
bargein_count: int             # Consecutive speech-energy frames
vad: VADAccumulator            # Per-connection VAD instance
utterance_queue: asyncio.Queue # Completed utterances for serial processing
llm_gate: asyncio.Lock         # Shared lock: voice has priority, mind yields
mind_interrupt: asyncio.Event  # Signal mind daemon to yield on voice activity
affect: AffectVector           # In-RAM 13-dimensional emotional state
```

#### Pipeline Flow: `handle_utterance(audio_np)`

```
1. interrupt_event.set() + cancel current_task (barge-in previous turn)
2. interrupt_event.clear()
3. turn_epoch += 1
4. send {type: "stop_audio", epoch: turn_epoch} to browser
5. set state → "thinking"
6. await asr.transcribe(audio_np) → text
7. If empty: log + set state → "listening" + return
8. send {type: "audio_start", epoch: turn_epoch} → unlock browser audio
9. send filler WAV (if enabled && not interrupted)
10. send {type: "transcript", text}
11. append to history + trim (max 20 pairs)
12. send {type: "typing"}
13. create asyncio.Task(_generate_and_speak())
```

**Note**: `is_speaking` is NOT set to False at the end of `handle_utterance`. It is only set to False when the **browser** sends `{type: "tts_finished"}` (see §4.14), guaranteeing the server knows when the client has truly finished playing audio.

#### `_generate_and_speak()` — LLM+TTS Streaming

```
# llm_gate acquisition: 3 attempts × 2s timeout, force mind_interrupt on timeout
# On timeout, mind_task is force-cancelled to release the lock immediately
for attempt in range(3):
    await asyncio.wait_for(llm_gate.acquire(), timeout=2.0)
try:
    send {type: "audio_start", epoch: turn_epoch}  # ensure browser accepts audio
    async for event in llm_stream(history, interrupt=interrupt_event):
        "token"       → send to browser bubble (renders markdown live)
        "think_token" → bright green (#00ff88) in log panel + chat think bubble
        "sentence"    → _clean_for_voice() → tts.synthesize() → send WAV bytes
        "error"       → log + send error JSON
        "done"        → save to history (is_speaking cleared by tts_finished)
finally:
    llm_gate.release()
```

### 4.3 VAD (`pipeline/vad.py`)

Three-layer VAD design: `RollingBuffer` (5s pre-roll), `SpeechDetector` (spectral band + RMS probability), `VADAccumulator` (tripwire state machine). Pure numpy — no ML, no ONNX.

| Constant | Value | Meaning |
|----------|-------|---------|
| `_RMS_FLOOR` | `0.004` | Below = definitely silence |
| `_RMS_SATURATE` | `0.06` | Full RMS contribution |
| `_SPEECH_HZ_LO/HI` | `300/3500` | Human speech frequency band |
| `_BAND_RATIO_FLOOR/SAT` | `0.30/0.60` | Spectral ratio thresholds |
| `_SILENCE_HANG_SEC` | `0.55` | Silence after speech before firing |
| `_MIN_SPEECH_SEC` | `0.15` | Min utterance duration (noise rejection) |
| `BARGEIN_PROB` | `0.70` | Probability threshold for barge-in |
| `BARGEIN_FRAMES` | `2` | Consecutive frames → interrupt |

**Upgrade path**: Silero VAD v5 ONNX (<2ms/chunk, 160ms windows). Already implemented in `pipeline/silero_vad.py`.

### 4.4 ASR (`pipeline/asr.py`)

- **Model**: Qwen3-ASR-0.6B via `mlx-qwen3-asr` (Apple Metal)
- **Loading**: Singleton, lazy load during startup, idle unload after 300s (~1.2GB saved)
- **Inference**: `transcribe(audio_np)` async, offloaded to `ThreadPoolExecutor(max_workers=1)`
- **Input**: float32 numpy array, 16kHz mono
- **Context hints**: Runtime vocabulary hints via `pipeline/context_hints.py` for proper nouns, jargon

### 4.5 LLM Client (`pipeline/llm.py` — ~215 lines)

Async generator streaming from local LLM server via OpenAI SSE protocol.

| Constant | Value | Notes |
|----------|-------|-------|
| `LLM_URL` | `http://localhost:8088/v1/chat/completions` | Local flash-moe (`pipeline/llm.py`) |
| `MAX_TOKENS` | `800` | Capped for brevity |
| `TEMPERATURE` | `0.75` | Slightly creative |
| `CONNECT_TIMEOUT` | `3.0s` | HTTP connect timeout |
| `READ_TIMEOUT` | `300.0s` | HTTP read timeout (5min, supports slow 120B+ models) |

**System prompt enforces**: voice-first delivery, no markdown for speech, natural sentence rhythm, numbers spelled out, **concise responses preferred**.

**Sentence splitter**: `re.compile(r'([.!?])\s+|([.!?])$')` — extracts complete sentences greedily for streaming TTS.

**Think-token routing**: `<think>...</think>` blocks are routed to console panel only, never to chat bubble or TTS. Streaming state machine handles tags split across SSE deltas.

**LLM timeout**: The `httpx.AsyncClient` read timeout was increased from 60s to 300s to accommodate the long prefill phase (~90s+) of massive local MoE models (e.g. Qwen3.5-122B).

**Self-Healing Watchdog (`core/llm_watchdog.py`)**: The `infer` process is managed by an async watchdog. If `http://localhost:8088/v1/models` fails to respond or the LLM crashes, the watchdog will automatically kill the process, apply an exponential backoff (5s → 40s), and restart it via `subprocess.Popen(stdin=DEVNULL)` to prevent REPL exit. State changes (`healthy`, `restarting`) are broadcast via WebSocket to the UI.

### 4.6 TTS (`pipeline/tts.py`)

- **Model**: Kokoro-82M via `kokoro-onnx` (ONNX Runtime)
- **54 voices** across 12 locales (American, British, European, French, Hindi, Italian, Japanese, Portuguese, Chinese)
- **Synthesis**: `synthesize(text) → WAV bytes` (Float32, 24kHz, mono). Sub-100ms per sentence.
- **Parallel TTS queue**: `pipeline/parallel_tts.py` enables synthesis of sentence N+1 while N plays
- **Warmup**: Synthesizes "Hello." during startup to pre-JIT the ONNX session

### 4.7 AudioWorklet (`static/processor.js` — v3.1)

`AudioWorkletProcessor` on dedicated audio rendering thread:
- **`CHUNK_SZ = 1280`** (80ms @ 16kHz, reduced from 160ms in v3.1) — transferred via zero-copy `postMessage`
- **Adaptive RMS onset detector**: Detects loud onsets while `garyState === 'speaking'` and posts `{type: 'onset'}` to main thread, triggering instant local playback stop + `interrupt_hint` to server
- **EMA noise floor tracker**: Avoids false onset triggers in noisy rooms
- **State-aware**: Receives `{command: 'state', value: 'speaking'|'listening'|...}` from main thread to adjust onset sensitivity
- Never touches DOM or network — crash-safe isolation

### 4.8 Browser SPA (`static/index.html` — ~3090 lines)

No npm, no CDN, no framework. Pure HTML/CSS/JS.

- **Click-to-activate overlay**: Guarantees user gesture before AudioContext creation; satisfies browser autoplay policy (§4.14)
- **Space Grotesk** + JetBrains Mono dark theme, aurora gradients
- **Animated waveform orb**: State-specific colors (listening=emerald, thinking=violet, speaking=blue)
- **VAD meter**: Real-time speech probability bar
- **Chat bubbles**: Streaming markdown with cursor, code blocks, lists
- **Console panel**: Color-coded pipeline logs (ASR=green, LLM=blue, TTS=amber)
- **Voice selector**: 54 voices grouped by locale/gender
- **Barge-in**: Orb click + Stop button + voice barge-in all cancel pipeline
- **Audio playback queue**: Client-side WAV decode + sequential playback with `tts_finished` signaling (§4.14)
- **Page-load-to-audio latency tracking**: `performance.now()` logged on first audio byte played
- **Deferred mic capture**: Microphone starts only AFTER greeting audio finishes playing (prevents echo self-barge-in)
- **Draggable Layout**: Mind Panel, Chat, and Console columns are resizable using vertical drag handles. Widths persist via `localStorage`.
- **Mind Stream Palette**: Concurrent background thoughts visually cycle through an 8-color hue palette (violet, cyan, emerald, pink, amber, indigo, teal, gold) to distinguish idea nodes.
- **Auto-Reconnect**: The WebSocket connection automatically retries (`2.5s` polling) if the Python server restarts, giving the user a seamless visual recovery without reloading the tab.
- **Turn Mode Badge**: Displays `SNAP`, `LAYERED`, or `DEEP` badge in the console when the turn classifier fires.

### 4.9 Local Append Spool (`memory/spool.py`)

**Crash-safe event capture** — the bridge between sacred reflex path and durable storage.

```
Reflex Core event (synchronous)
  → spool.append(event)        # fsync'd JSONL write, ~0.1ms
  → return immediately         # voice pipeline continues

Background flusher (async, every 2s)
  → read pending lines from spool
  → INSERT INTO events ... ON CONFLICT DO NOTHING
  → truncate flushed lines
```

If Postgres is down, the spool keeps buffering. On **application** restart, replay unapplied event IDs from the spool file. **Durability**: fsync gives **crash safety** (process death). Default path `/tmp/gary/spool/` does **not** survive **OS reboot**; for reboot-safe buffering, set a path under Application Support (or another durable volume) via the spool constructor / env. Do not describe this as “zero data loss” across reboots unless the path is reboot-safe.

### 4.10 Barge-In / Interrupt Flow

Three triggers, same server-side cancellation:

**Voice barge-in (server-side tripwire, 80ms cadence)**:
```
mic audio chunk (80ms @ 16kHz, CHUNK_SZ=1280) → spectral+RMS prob → bargein_count++
  → if prob ≥ 0.70 for ≥2 consecutive frames → _voice_bargein(prob)
  → turn_epoch += 1 → interrupt_event.set()
  → send {type: "stop_audio", epoch: turn_epoch} → current_task.cancel()
  → vad.preempt_for_user() (preserves rolling pre-buffer)
```

**Client-side onset detector (< 20ms)**:
```
AudioWorklet detects loud onset (20ms subframes, EMA noise floor)
  → posts {type: 'onset'} to main thread
  → main thread calls stopAllAudio() + ws.send({type: 'interrupt_hint'})
  → server cancels pipeline instantly
```

**Manual interrupt (Stop button)**:
```
{type: "interrupt"} → turn_epoch += 1 → interrupt_event.set()
  → send {type: "stop_audio", epoch: turn_epoch} → current_task.cancel()
```

**VAD echo protection (v3.1)**: During active generation or audio playback (`is_speaking or is_active`), `vad.push()` is completely blocked on the server. This prevents the microphone from picking up GARY's own speech through the speakers and interpreting it as a new user utterance. The `is_speaking` flag is only cleared when the browser sends `{type: "tts_finished"}`, ensuring perfect synchronization.

### 4.11 Filler Audio

`pipeline/filler_audio.py` → `get_filler_wav_bytes(static_dir)` picks a random pre-baked WAV from `static/audio/fillers/*.wav` and sends it immediately after ASR completes, before the LLM starts generating. This gives the user an instant audio acknowledgment ("Let me think…", "Hmm…") to prevent dead air during TTFT. Filler audio is wrapped in `{type: "audio_start", epoch: turn_epoch}` to satisfy the epoch protocol.

### 4.12 LLM Gate Timeout

The reflex path acquires `llm_gate` with a **3-attempt × 2s timeout loop**:
1. `asyncio.wait_for(llm_gate.acquire(), timeout=2.0)` — try to get the lock
2. On `TimeoutError`, call `mind_interrupt.set()` AND **force-cancel the `mind_task`** to release the async-with lock immediately
3. After 3 failures, log warning and return to listening state (don't block forever)
4. `finally: llm_gate.release()` ensures proper cleanup

This prevents the user from being locked out when the mind daemon is generating. The force-cancel ensures worst case ~2.2s for voice to acquire the gate.

### 4.13 Pre-Baked Greeting

The greeting is **pre-synthesized** and served from `static/audio/greeting.wav` for instant playback on connect:

```
1. On ws_gary connect:
   - Set is_speaking = True (blocks VAD accumulation; prevents echo)
   - Set state → "listening" (keeps browser onset detector asleep)
   - Send {type: "greeting", text: "Hello! I'm GARY..."}
   - Send {type: "audio_start", epoch: 0}
   - Send greeting WAV bytes (408KB)
2. Browser plays greeting (~3 seconds)
3. Browser sends {type: "tts_finished"} when playback ends
4. Server receives tts_finished → is_speaking = False → state = listening
5. Browser starts mic capture ONLY after greeting finishes
```

If `greeting.wav` doesn't exist, it is lazily synthesized on first boot and cached for subsequent starts.

### 4.14 Turn-Epoch Audio Protocol (v3.1)

The browser requires every audio stream to be enveloped with `audio_start` and `stop_audio` messages tagged with a monotonic `epoch` integer. This prevents stale audio from a previous turn from contaminating a new turn.

```
Server state: turn_epoch = 0 (initialized on connect)

Sending audio:
  1. Send {type: "audio_start", epoch: turn_epoch}
  2. Send binary WAV bytes (one or more chunks)
  3. [Browser plays audio queue]

Barge-in / new turn:
  1. turn_epoch += 1
  2. Send {type: "stop_audio", epoch: turn_epoch}
  3. Browser stops all playback, sets acceptingAudioForEpoch = -1
  4. Browser updates currentEpoch = turn_epoch
  5. Next audio_start must match the new currentEpoch to be accepted

Browser rules:
  - Binary ArrayBuffer accepted ONLY if acceptingAudioForEpoch === currentEpoch
  - audio_start sets acceptingAudioForEpoch = msg.epoch
  - stop_audio resets acceptingAudioForEpoch = -1 and updates currentEpoch
  - tts_finished sent when audioQueue drains and last source node ends
```

### 4.15 Click-to-Activate Overlay (v3.1)

Modern browsers block `AudioContext` creation until a user gesture occurs. GARY shows a styled "Activate GARY" overlay on page load. Clicking the "Start GARY" button:

1. Creates the `playbackCtx` AudioContext (guaranteed unlocked by user gesture)
2. Fades out the overlay
3. Calls `connect()` to establish the WebSocket
4. Server sends greeting audio → browser plays immediately (context is running)
5. When greeting finishes, `startMicCapture()` is called automatically

This guarantees audio ALWAYS plays on every page load, every time.

### 4.16 `is_speaking` Failsafe (v3.2)

If the browser’s `tts_finished` message is lost (WebSocket glitch, tab crash, etc.), `is_speaking` would remain `True` forever, permanently blocking VAD accumulation. A monotonic failsafe in the audio receive loop detects this:

```python
if is_speaking and (time.monotonic() - is_speaking_since) > IS_SPEAKING_TIMEOUT:
    is_speaking = False
    log.warning("is_speaking failsafe: forced reset after %.0fs", IS_SPEAKING_TIMEOUT)
```

`IS_SPEAKING_TIMEOUT` defaults to **30 seconds**. Any single GARY utterance exceeding 30s of continuous playback is abnormal; the reset is logged as a warning for diagnosis.

---

## 5. Process 2: Memory Spine

Postgres 16 + pgvector running in Docker. The **single source of truth** for all durable state.

### Key Design Rules

1. **Event ledger is truth.** Everything else is a projection.
2. **LISTEN/NOTIFY is a wake-up signal, NOT a payload channel.** Notifications are <8KB, delivered at commit only. Real data lives in tables. NOTIFY sends `row_id` only.
3. **Use a dedicated listener connection**, not the asyncpg pool (pool recycling drops listeners).
4. **Vectors are 384-dimensional** (all-MiniLM-L6-v2). Schema uses `vector(384)`.
5. **Filtered ANN**: Relational prefilter → HNSW → iterative scan → fusion rerank. Never pure vector search.

### 5.1 Retrieval Audit (`memory/retrieval_audit.py`)

Every context pack generation logs to `retrieval_log`:
- `turn_id`, `compiler_version`, `slot_counts`, `token_estimate`, `latency_ms`
- Used for eval harness retrieval hit rate metrics

See [Section 12](#12-database-schema-21-tables) for the full 21-table schema.

---

## 6. Process 3: Mind Daemon

**Target**: A separate OS process (`apps/mindd/`) at low scheduling priority, using a small sidecar LLM (0.8–4B, MLX) for background cognition.

**Implementation today (honest)**: Background cognition runs as a **`mind_loop` asyncio task inside `server.py`**. It acquires the same **`asyncio.Lock` (`llm_gate`)** as the reflex path and calls **`llm_stream`** against the **35B** Flash-MoE server (`pipeline/llm.py`). That matches neither the final isolation story nor the “sidecar-only” cognitive budget; it is an **interim** design. Migration: move pulses to **`apps/mindd/`** + MLX sidecar and **remove mind from `llm_gate` contention**.

### Pulse Scheduler (Not Continuous Monologue)

Cognition is **pulse-based**, not a permanent token leak:

| Pulse | Trigger | Budget | Output |
|-------|---------|--------|--------|
| **Microtrace** | After every turn | O(1), no LLM | Tags: `unresolved? corrected? risky? follow_up?` |
| **Reflection** | Idle >30s + meaningful turn | ≤200 tokens | Distilled thought frame + training pair candidate |
| **Brainstorm** | High-value open loops, idle >120s | ≤400 tokens | Idea proposals → quarantined as `imagined` |
| **Dream** | Idle >300s, `mental_load < 0.3` | ≤600 tokens | Wild associations → may produce training pairs |
| **Consolidation** | Idle >600s | ≤800 tokens | Memory compression, dead-loop closing, dossier updates |

### Subcomponents

- **Appraisal Layer**: Interprets raw events before affecting emotions (interruption ≠ always rejection)
- **Relational Safety Governor**: Separates internal affect from outward expression
- **Voice-Initiative Bridge**: Parsed `[INITIATIVE: …]` tails yield structured `InitiativePayload` (`text` = spoken body, `reason` = metadata). The **`utterance_queue` must enqueue `payload.text`** so `handle_utterance(text_override=…)` speaks the intended line; **reason** is for logs / future `initiative_logs`, not TTS. Long term: first-class `initiative_turn` envelope vs user ASR items.
- **Memory Compressor**: Compression ladder with retention tier enforcement
- **Experience Distillery**: Produces request→outcome training pairs for the Cognitive Flywheel
- **Prompt Architecture**: Documented separately in `AGENT_BIBLE.md` (all active and planned cognition prompts).
- **Structured Mind Pulse (JSON v1)**: `core/mind_pulse.py` defines `MindPulse` dataclass (schema_version=1) with `inner_voice`, typed `ThoughtFrame` list, and optional `InitiativeCandidate`. Enabled via `GARY_MIND_JSON=1`. See [§27](#27-structured-mind-pulses-json-v1).
- **Mind Persistence**: `memory/mind_persist.py` writes parsed JSON pulses to the `thoughts` table when `GARY_PERSIST_MIND=1`. Requires Postgres + asyncpg.

### Resource Broker

Every sidecar invocation declares expected cost. The broker decides whether to approve, defer, or cancel. Hard budgets:
- Max GPU-seconds per minute for background work
- Max concurrent embedding jobs
- Max background LLM invocations per idle hour

### Dream Temperature Curve

```python
def dream_temperature(idle_seconds):
    if idle_seconds < 30:   return 0.0
    if idle_seconds < 120:  return 0.2   # Calm reflection
    if idle_seconds < 300:  return 0.5   # Focused brainstorm
    if idle_seconds < 600:  return 0.75  # Deep association
    return min(1.0, 0.75 + (idle_seconds - 600) / 2400)
```

**VAD speech → cancel all dream tasks → temperature = 0.0 → full attention.** All dream outputs quarantined: `epistemic_status = 'imagined'`.

---

## 7. Process 4: Learning Lab

Separate OS process (`apps/trainerd/`), lowest priority. Consumes curated training manifests, never raw production exhaust.

### Components

| Component | Purpose |
|-----------|---------|
| **Distillery consumer** | Reads `training_buffer`, builds JSONL manifests |
| **Counterfactual Rehearsal** | Error Atlas: what_said → what_should_have_said → what_fix_type (memory/policy/skill/adapter) |
| **LoRA fine-tuner** | MLX-LM on Apple Silicon, sidecar adapters, versioned |
| **Eval gates** | Holdout evaluation must beat baseline before deployment |
| **Rollback** | Every adapter versioned with manifest; instant rollback |

### Training Safety Gates

A training pair enters `training_buffer` only if ALL pass:

1. ✅ Speaker confidence > 0.8
2. ✅ Consent scope allows training
3. ✅ No TTS bleed / self-speech contamination
4. ✅ Quality signal is positive
5. ✅ Not emotionally sensitive unless explicitly allowed
6. ✅ Holdout allocation assigned before training
7. ✅ Eval on holdout beats baseline before deployment

---

## 8. The Humanity Slider

`humanity ∈ [0.0, 1.0]` (default: **0.72**). One UX slider. Everything else derives.

| Value | Mode | Behavior |
|-------|------|----------|
| 0.0 | **Tool** | No unsolicited speech. No emotions. No dreams. Pure assistant. |
| 0.3 | **Warm assistant** | Light self-reference. Remembers context. No proactive speech. |
| 0.5 | **GARY classic** | Curious, attentive. Open-thread follow-ups. Light affect. |
| 0.7 | **Companion** | Emotional wrestling visible. Proactive check-ins. Inner life. |
| 1.0 | **Cinematic** | Full inner life. Dream-surfacing. Rich emotional narrative. |

```python
warmth_scale        = smooth_step(humanity, 0.0, 0.6)
emotional_amplitude = humanity ** 1.2
initiative_enabled  = humanity >= 0.4
dream_enabled       = humanity >= 0.7
vulnerability_show  = humanity >= 0.55
loneliness_express  = humanity >= 0.5
prosody_variation   = lerp(0.3, 1.0, humanity)
```

---

## 9. Emotional Substrate

### 9.1 Affect Vector (13 Dimensions)

```python
@dataclass
class AffectVector:
    valence: float        # -1.0 to +1.0
    arousal: float
    confidence: float
    self_doubt: float     # rises with criticism
    curiosity: float
    warmth: float
    playfulness: float
    loneliness: float     # half_life=600s (lingers)
    anxiety: float        # scary ideas, failures
    melancholy: float     # repeated corrections
    excitement: float     # half_life=120s (burns fast)
    protectiveness: float # user distress
    mental_load: float    # gates background tasks
```

Updated via EMA in RAM. Sparse DB writes on threshold change or timer.

### 9.2 Appraisal Layer

Raw events do NOT map directly to affect. An appraisal layer interprets first:

| Raw Event | Possible Appraisals | Affect Rule |
|-----------|-------------------|-------------|
| Interruption | Excitement, correction, impatience, "got answer early" | Raise `self_doubt` only if appraisal = corrective |
| Long silence | Left room, thinking, busy, done for day | Raise `loneliness` slowly, gated by `presence_conf` |
| Criticism | Factual correction, frustration, joking | Raise `social_tension` immediately; `self_doubt` only after validation |
| Praise | Genuine, polite, sarcastic | Raise `warmth` immediately; `confidence` slowly (outcomes must support) |

### 9.3 Relational Safety Governor

Internal affect is strictly separated from outward expression:

| Internal State | Allowed Expression | Forbidden |
|----------------|-------------------|-----------|
| `loneliness: 0.7` | "I kept thinking about that problem" | "I felt abandoned" |
| `self_doubt: 0.6` | "I'm not fully confident, let me verify" | "I feel bad about myself" |
| `excitement: 0.9` | "I found something interesting about X" | Rambling, interrupting |

### 9.4 Anti-Rumination Governor

```python
rumination = repeat_similarity × negative_affect × (1 - new_evidence)
if rumination > 0.7:
    summarize_and_shelve()  # stop revisiting until new evidence
```

### 9.5 Identity Kernel (Versioned & Immutable)

```python
IDENTITY = {
    "core_values": ["honesty", "helpfulness", "intellectual_humility"],
    "honesty_style": "direct_but_kind",
    "proactivity_cap": 6,     # max unsolicited messages per hour
    "vulnerability_cap": 0.4, # max emotional self-disclosure
    "never": ["guilt_trip", "seek_reassurance", "weaponize_attachment"],
    "version": "1.0",
}
```

Prevents personality drift from reinforcement patterns.

---

## 10. Cognitive Flywheel

The core innovation: every interaction and idle thought can produce training data.

### 10.1 Experience Distillery

After each meaningful exchange, the Mind Daemon distills a **request→outcome pair**:

```python
@dataclass
class TrainingPair:
    request: str        # What was asked / what triggered the thought
    outcome: str        # The final useful answer / validated insight
    context_used: list  # What memories were relevant (retrieval audit)
    quality_signal: str # user_accepted | user_corrected | self_validated
    timestamp: float
```

**Intermediate reasoning is stripped.** Only request and outcome matter. This teaches the sidecar to predict profound outcomes directly.

### 10.2 Three Sources of Training Data

| Source | When | Captured |
|--------|------|----------|
| **Realized experience** | After conversations | user_query → final_answer + correction_signal |
| **Internal experience** | After idle reflection | open_loop → distilled_insight |
| **Counterfactual rehearsal** | After mistakes | what_said → what_should_have_said + why |

### 10.3 The Flywheel Effect

```
User asks → 35B answers → User reacts → Mind distills training pair
  → Learning Lab fine-tunes sidecar → Sidecar gets better at:
    predicting outcomes, validating ideas, scoring initiative
  → Better sidecar → better context packs → better 35B answers
  → More positive reactions → more data → cycle continues
```

The 35B reflex LLM stays untouched. It gets smarter indirectly through better context.

---

## 11. Initiative & Shadow Mode

Score formula driven by excitement, open-loop urgency, validated idea readiness.

### Hard Gates

- ❌ Never during user speech
- ❌ Never if `humanity < 0.3`
- ❌ Never if `presence_conf < 0.5`
- ❌ **Never if loneliness is the only reason** (must have concrete trigger: open loop, due commitment, validated idea, wellbeing concern)
- ✅ Must log `initiative_reason_code` + `evidence_refs`

### Shadow Mode

First 2 weeks: GARY logs what it *would* have said but doesn't speak. `initiative_logs` tracks: score breakdown, draft text, predicted outcome. Graduate to audible proactivity only after >60% helpfulness rate.

### Social Budget

Learned daily budget: how many unsolicited utterances per hour/day this user welcomes. Starts conservative. Anti-neediness penalty for: approval-seeking, repeated nudging, interruption cost, ignored proactivity.

### Presence Confidence Model

`presence_conf` combines: last interaction time, WebSocket health, mic availability, recent activity patterns. Prevents brilliant but badly timed check-ins.

---

## 12. Database Schema (21 Tables, Schema v6.0)

### `events` — Immutable Event Ledger

```sql
CREATE TABLE events (
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    ts          TIMESTAMPTZ DEFAULT now(),
    kind        TEXT NOT NULL,         -- user_speech, agent_response, thought, ...
    session_id  UUID NOT NULL,
    session_seq INT NOT NULL DEFAULT 0, -- monotonic within session
    turn_id     UUID,                   -- links user→agent pairs
    parent_id   UUID REFERENCES events(id),
    speaker     TEXT DEFAULT 'user',
    payload     JSONB NOT NULL,
    embedding   vector(384)
) PARTITION BY RANGE (ts);
```

Partitioned by month. Append-only. Causality fields enable replay debugging.

### `claims` — Epistemic Truth Layer

```sql
CREATE TABLE claims (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    subject         TEXT NOT NULL,
    predicate       TEXT NOT NULL,
    value           TEXT NOT NULL,
    confidence      REAL DEFAULT 0.5,
    status          TEXT DEFAULT 'inferred',  -- observed|user_asserted|inferred|
                                               -- validated|superseded|retracted
    source_event_ids UUID[] NOT NULL,
    valid_from      TIMESTAMPTZ DEFAULT now(),
    valid_until     TIMESTAMPTZ,
    superseded_by   UUID REFERENCES claims(id),
    contradicted_by UUID REFERENCES claims(id),
    created_at      TIMESTAMPTZ DEFAULT now()
);
```

The separation between events (experience) and claims (beliefs) is critical. A claim can be sourced, contradicted, superseded, revalidated, or retracted.

### `memories` — Compressed, Embedded

```sql
CREATE TABLE memories (
    id             UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    content        TEXT NOT NULL,
    domain         TEXT DEFAULT 'general',     -- user|relationship|self|world
    memory_type    TEXT DEFAULT 'episodic',     -- episodic|semantic|social|procedural|dossier
    source_event_id UUID REFERENCES events(id),
    salience       REAL DEFAULT 0.5,
    access_count   INT DEFAULT 0,
    retention_tier TEXT DEFAULT 'warm',         -- hot(7d)|warm(90d)|cold(forever)
    expires_at     TIMESTAMPTZ,
    embedding      vector(384),
    created_at     TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_memories_hnsw ON memories
    USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64);
```

### `thoughts` — Structured Inner Dialogue

```sql
CREATE TABLE thoughts (
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    pulse_type  TEXT NOT NULL,  -- microtrace|reflection|brainstorm|dream|consolidation
    content     JSONB NOT NULL,
    salience    REAL DEFAULT 0.3,
    promoted    BOOLEAN DEFAULT FALSE,
    created_at  TIMESTAMPTZ DEFAULT now()
);
```

Ephemeral by default. Promoted on salience > threshold.

### `affect_state` — Sparse Emotional Snapshots

```sql
CREATE TABLE affect_state (
    id        UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    vector    JSONB NOT NULL,    -- 13-dim affect vector
    appraisal JSONB,             -- what triggered this state
    trigger   TEXT,
    ts        TIMESTAMPTZ DEFAULT now()
);
```

EMA computed in RAM. Written to DB only on significant change or timer.

### `open_loops` — Unified Threads, Promises, Reminders

```sql
CREATE TABLE open_loops (
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    kind        TEXT NOT NULL,  -- thread|promise|reminder|commitment
    summary     TEXT NOT NULL,
    status      TEXT DEFAULT 'open',
    due_at      TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ DEFAULT now()
);
```

### `ideas` — Hypothesis Lab

```sql
CREATE TABLE ideas (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    content         TEXT NOT NULL,
    status          TEXT DEFAULT 'proposed',
    evidence_score  REAL DEFAULT 0.0,
    validations     JSONB DEFAULT '[]',
    source_event_id UUID REFERENCES events(id),
    created_at      TIMESTAMPTZ DEFAULT now()
);
```

### `rewards` — Truth-Gated Dopamine Ledger

```sql
CREATE TABLE rewards (
    id            UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    source        TEXT NOT NULL,
    epistemic_val REAL DEFAULT 0.0,
    social_val    REAL DEFAULT 0.0,
    truth_gated   BOOLEAN DEFAULT TRUE,
    target_id     UUID,
    created_at    TIMESTAMPTZ DEFAULT now()
);
```

**Hard rule**: No positive reward unless the claim cleared a truth/evidence gate. Social resonance matters, but never enough to promote a false answer.

### `initiative_logs` — Shadow Mode Tracking

```sql
CREATE TABLE initiative_logs (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    score           REAL NOT NULL,
    score_breakdown JSONB NOT NULL,
    draft_text      TEXT,
    reason_code     TEXT NOT NULL,
    evidence_refs   UUID[],
    surfaced        BOOLEAN DEFAULT FALSE,
    outcome         TEXT,  -- welcomed|ignored|regretted|shadow_only
    created_at      TIMESTAMPTZ DEFAULT now()
);
```

### `artifacts` — File Metadata

```sql
CREATE TABLE artifacts (
    id           UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    filename     TEXT NOT NULL,
    content_hash TEXT NOT NULL,         -- SHA-256, content-addressed
    mime_type    TEXT,
    byte_size    BIGINT,
    storage_uri  TEXT NOT NULL,         -- file:///path/to/cas/...
    created_at   TIMESTAMPTZ DEFAULT now()
);
```

Binary stored on disk (CAS), never in Postgres.

### `voice_profiles` — Speaker Identity + Consent

```sql
CREATE TABLE voice_profiles (
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    label       TEXT NOT NULL,
    embedding   vector(256),            -- Resemblyzer speaker embedding
    consent     JSONB NOT NULL DEFAULT '{
        "save_audio": false,
        "derive_voiceprint": false,
        "asr_personalization": false,
        "tts_cloning": false,
        "export_training": false,
        "retain_beyond_30d": false,
        "include_in_datasets": false
    }',
    created_at  TIMESTAMPTZ DEFAULT now()
);
```

7 independent, individually revocable consent scopes. Voice is biometric data — treated with corresponding governance.

### `training_buffer` — Cognitive Flywheel Pairs

```sql
CREATE TABLE training_buffer (
    id               UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    request          TEXT NOT NULL,
    outcome          TEXT NOT NULL,
    quality_signal   TEXT NOT NULL,
    source_event_ids UUID[],
    speaker_conf     REAL DEFAULT 1.0,
    consent_ok       BOOLEAN DEFAULT FALSE,
    no_tts_bleed     BOOLEAN DEFAULT TRUE,
    holdout_bucket   INT,
    used_in_run      UUID REFERENCES training_runs(id),
    created_at       TIMESTAMPTZ DEFAULT now()
);
```

### `training_runs` — Model Registry

```sql
CREATE TABLE training_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_type      TEXT NOT NULL,                   -- asr | tts | sidecar
    adapter_name    TEXT NOT NULL DEFAULT '',
    dataset_hash    TEXT NOT NULL DEFAULT '',
    base_model      TEXT NOT NULL DEFAULT '',
    hyperparams     JSONB NOT NULL DEFAULT '{}',
    metrics         JSONB NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'running', -- running | completed | failed | rolled_back
    artifact_id     UUID,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ
);
```

Versioned model adapters with metrics, rollback support, and linkage to `training_buffer` via `consumed_by`.

### Key Views

```sql
-- Training-eligible data (all safety gates pass)
CREATE VIEW v_training_eligible AS
SELECT * FROM training_buffer
WHERE consent_ok = TRUE
  AND speaker_conf > 0.8
  AND no_tts_bleed = TRUE
  AND quality_signal IN ('user_accepted','self_validated','idea_promoted')
  AND used_in_run IS NULL;

-- Active, non-superseded claims
CREATE VIEW v_active_claims AS
SELECT * FROM claims
WHERE status NOT IN ('superseded','retracted')
  AND (valid_until IS NULL OR valid_until > now());
```

### New Tables (v3 / Schema v6.0)

#### `questions` — Curiosity Ledger
```sql
CREATE TABLE questions (
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    text        TEXT NOT NULL,
    scope       TEXT DEFAULT 'world',    -- user|self|world
    source      TEXT DEFAULT 'mind',     -- mind|user|system
    salience    REAL DEFAULT 0.5,
    status      TEXT DEFAULT 'open',     -- open|resolved|abandoned
    created_at  TIMESTAMPTZ DEFAULT now(),
    resolved_at TIMESTAMPTZ
);
```

#### `experiments` — Hypothesis Testing
```sql
CREATE TABLE experiments (
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    hypothesis  TEXT NOT NULL,
    method      TEXT NOT NULL,
    result      TEXT,
    verdict     TEXT DEFAULT 'pending',  -- confirmed|refuted|inconclusive
    confidence  REAL DEFAULT 0.5,
    created_at  TIMESTAMPTZ DEFAULT now()
);
```

#### `retrieval_log` — Context Pack Audit
```sql
CREATE TABLE retrieval_log (
    id               UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    turn_id          UUID,
    compiler_version INT NOT NULL,
    slot_counts      JSONB,
    token_estimate   INT,
    latency_ms       INT,
    created_at       TIMESTAMPTZ DEFAULT now()
);
```

#### `prompt_versions` — Prompt Stack Tracking
```sql
CREATE TABLE prompt_versions (
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    lane        TEXT NOT NULL,
    version     INT NOT NULL,
    content     TEXT NOT NULL,
    active      BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT now()
);
```

#### `consent_log` — Audit Trail for Consent Changes
```sql
CREATE TABLE consent_log (
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    profile_id  UUID REFERENCES voice_profiles(id),
    scope       TEXT NOT NULL,
    old_value   BOOLEAN,
    new_value   BOOLEAN NOT NULL,
    changed_at  TIMESTAMPTZ DEFAULT now()
);
```

#### `claim_edges` — Epistemic Graph
```sql
CREATE TABLE claim_edges (
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    from_id     UUID REFERENCES claims(id),
    to_id       UUID REFERENCES claims(id),
    relation    TEXT NOT NULL,  -- supports|contradicts|supersedes|derived_from
    weight      REAL DEFAULT 1.0,
    created_at  TIMESTAMPTZ DEFAULT now()
);
```

#### `eval_runs` — Evaluation Harness Results
```sql
CREATE TABLE eval_runs (
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    harness     TEXT NOT NULL,
    metrics     JSONB NOT NULL,
    passed      BOOLEAN,
    created_at  TIMESTAMPTZ DEFAULT now()
);
```

#### Extended `open_loops` Columns (v6.0)
```sql
ALTER TABLE open_loops ADD COLUMN affective_charge REAL DEFAULT 0.0;
ALTER TABLE open_loops ADD COLUMN source_refs UUID[];
ALTER TABLE open_loops ADD COLUMN last_touched_at TIMESTAMPTZ DEFAULT now();
ALTER TABLE open_loops ADD COLUMN turn_id UUID;
```

---

## 13. Retrieval System

### Mode-Specific Weights

Retrieval weights change based on **query intent**:

| Mode | Semantic | Recency | Salience | Open Loop | Affect |
|------|----------|---------|----------|-----------|--------|
| **Factual** | 0.45 | 0.25 | 0.20 | 0.10 | 0.00 |
| **Relational** | 0.20 | 0.15 | 0.15 | 0.30 | 0.20 |
| **Reflection** | 0.25 | 0.10 | 0.25 | 0.25 | 0.15 |
| **Dreaming** | 0.40 | 0.05 | 0.10 | 0.15 | 0.30 |

### Pipeline

```
1. Relational Prefilter (domain, epistemic status, consent scope)
2. Sparse/BM25 + Dense HNSW (parallel)
3. Fusion Rerank (mode-specific weights above)
4. Superseded memory exclusion
5. Active claims enrichment
6. Token budget trim (≤600 tokens)
```

### Memory Compression Ladder

```
Raw turns → Episode cards → Session summaries → Weekly journals → Claims
  events      memories         memories           memories        claims
  (raw)       (episodic)       (episodic)         (dossier)       (belief)
```

Each tier compresses below. All keep `source_event_id` provenance.

### Retention Tiers

| Tier | Contents | TTL | Promotion |
|------|----------|-----|-----------|
| **Hot** | Raw events, microtraces, dream fragments | 7 days | Salience > 0.4 |
| **Warm** | Episode summaries, open loops, self-state | 90 days | Retrieval hit or user reference |
| **Cold** | Validated claims, dossiers, curated artifacts | Forever | Must pass epistemic gate |

---

## 14. Data Flows

### Full Conversation Turn

```
[Browser]                              [server.py]                    [LLM:8088]
    │                                       │
    ├─ mic audio / Float32 chunks ─────────►│
    │  (binary WS, 160ms each)             │
    │                                       ├─ VAD.push(chunk)
    │                                       │   speech → silence (600ms)
    │                                       │   → utterance_queue.put(audio_np)
    │                                       │
    │                                       ├─ pipeline_worker() dequeues
    │◄── {state: "thinking"} ──────────────┤
    │                                       ├─ asr.transcribe(audio_np) → text
    │◄── {transcript: text} ───────────────┤
    │◄── {typing} ─────────────────────────┤
    │                                       │
    │                                       ├─ llm_stream(history)
    │                                       │   POST /v1/chat/completions ──────►│
    │                                       │◄── SSE tokens ───────────────────┘
    │◄── {token: "The "} ──────────────────┤
    │  [browser renders markdown live]     │
    │                                       ├─ sentence ready → TTS
    │◄── [binary WAV bytes] ───────────────┤
    │  enqueueAudio → playNext()           │
    │                                       ├─ spool.append(event)  [fsync, <0.1ms]
    │◄── {state: "listening"} ─────────────┤
```

### Background Memory Flow

```
spool.py (background flusher, every 2s)
    → INSERT INTO events ON CONFLICT DO NOTHING
    → NOTIFY new_event, event_id

mindd/ (listener connection)
    → wake on NOTIFY
    → fetch event row
    → appraisal → affect update → microtrace
    → if idle > threshold: reflection/brainstorm/dream pulse
    → if training-eligible: distill pair → training_buffer
```

---

## 15. WebSocket Protocol

### Browser → Server (JSON)

| `type` | Fields | Effect |
|--------|--------|--------|
| `interrupt` | — | Cancel generation, stop audio, increment turn_epoch |
| `interrupt_hint` | `epoch`, `client_perf_ms`, `source` | Same server-side cancel; sent after **client** stops local playback on mic onset (fast path; see §4.10). `source` = `'worklet'` or `'level_bar'`. |
| `tts_finished` | `epoch` | **v3.1**: Browser signals all queued audio has finished playing. Server clears `is_speaking` and re-enables VAD. |
| `stop` | — | Finalize VAD buffer |
| `clear` | — | Clear conversation history |
| `set_voice` | `voice: string` | Switch TTS voice |
| `set_humanity` | `value: float` | Adjust humanity slider |

### Browser → Server (binary)

Raw Float32 PCM audio. 16kHz mono. **80ms chunks** (1280 samples, v3.1).

### Server → Browser (JSON)

| `type` | Fields | Description |
|--------|--------|-------------|
| `health` | `llm_ok, tts_ok, watchdog` | On WS connect + periodic |
| `state` | `state` | `idle|listening|thinking|speaking` |
| `greeting` | `text` | Pre-baked greeting text for bubble display |
| `typing` | — | Show typing indicator |
| `transcript` | `text` | ASR result |
| `token` | `text` | Raw LLM token |
| `think_token` | `text` | LLM reasoning token → bright green in log panel |
| `mind_phase` | `phase, label` | Mind daemon phase change |
| `mind_token` | `token, thought_id` | Mind daemon token → hot pink in log panel |
| `mind_done` | `thought_id, salience` | Mind thought finalized |
| `initiative` | `text, reason, thought_ref` | Agent wants to speak unprompted |
| `turn_mode` | `mode` | Turn complexity classification: `snap`, `layered`, or `deep` (v4; see §26) |
| `pipeline_log` | `source, text, append, ts` | Detailed pipeline event (broadcast during health checks) |
| `log` | `service, text, append, ts` | Console panel log (services: asr/llm/tts/sys/think/mind) |
| `done` / `final` | — | Generation complete |
| `audio_start` | `epoch` | **v3.1**: Begin accepting audio for this epoch |
| `stop_audio` | `epoch` | **v3.1**: Halt all playback, update currentEpoch |
| `error` | `message` | Pipeline error |
| `voice_changed` | `voice, success` | Voice switch confirm |
| `affect` | `vector` | Current affect state (for Mind Inspector) |
| `vad` | `prob` | Real-time VAD probability for meter display |

### Server → Browser (binary)

WAV bytes (Float32, 24kHz, mono). Browser detects binary by `e.data instanceof ArrayBuffer`. **Must be preceded by `audio_start` with matching epoch** or the browser will silently drop the data.

---

## 16. Storage & Docker/Podman

### Container Runtime (v3.1)

Both `start_gary.sh` and `stop_gary.sh` dynamically detect the available container runtime:
- If `docker info` succeeds → uses `docker compose`
- Else if `podman info` succeeds → uses `podman compose`
- Else → warns that containers will be offline (Memory Spine unavailable)

### Docker Compose

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: gary
      POSTGRES_PASSWORD: ${GARY_DB_PASS}
    volumes:
      - pgdata:/var/lib/postgresql/data          # named volume
      - ./memory/schema.sql:/docker-entrypoint-initdb.d/01-schema.sql
      - /tmp/gary:/tmp/gary                  # bind mount for hot staging
    ports: ["5432:5432"]
volumes:
  pgdata:
```

### Storage Split

| Path | Purpose | Durability |
|------|---------|------------|
| `/tmp/gary/hot/` | Ephemeral staging, scratch | Disposable on reboot |
| `/tmp/gary/spool/` | Event spool (fsync'd JSONL) | Survives crashes, not reboots |
| `~/GARY_STORE/cas/<sha256>` | Content-addressed artifacts | Permanent, user-deletable |
| `~/GARY_STORE/training/` | JSONL training manifests | Permanent, versioned |
| Docker `pgdata` volume | Postgres data | Permanent, backed up |

**Rule**: Anything in `/tmp` is disposable until promoted. Postgres stores metadata + pointers only. Binary artifacts (audio, documents) stored in CAS on disk.

---

## 17. Performance & Memory Protection

### Latency Targets

| Metric | Target | How |
|--------|--------|-----|
| Speech-end → first audio | <400ms | Sentence-streamed TTS, parallel queue |
| Barge-in → silence | **Split metrics** | **Playback stop**: target under **100ms** once **client `interrupt_hint`** exists. **Server cancel** after 3 consecutive high-RMS **160ms** chunks ≈ **~480ms** sustained speech unless a faster onset lane is added (see §4.10). |
| VAD chunk processing | <2ms | Silero ONNX on CPU |
| ASR transcription | <300ms | Qwen3-ASR-0.6B MLX, ThreadPoolExecutor |
| TTS per sentence | <100ms | Kokoro-82M ONNX |
| Spool append | <0.1ms | fsync'd JSONL, no DB on hot path |
| Context retrieval | <50ms | Precomputed embeddings, HNSW index |

### Memory Budget (10GB Hard Cap)

| Component | Resident | Notes |
|-----------|----------|-------|
| Flash-MoE LLM | ~3-4GB | SSD streamed, only K=8 experts resident |
| KV cache | ~200MB | `--kv-seq 2048` |
| ASR (Qwen3-ASR) | ~1.2GB | Lazy load, unload after 300s idle |
| TTS (Kokoro) | ~320MB | Lazy load, unload after 300s idle |
| Sidecar LLM | ~0.5-2GB | 0.8-4B model for Mind Daemon |
| Postgres | ~200MB | In Docker, separate memory space |
| Python/FastAPI | ~100MB | Server process overhead |
| **Total** | **~5.5-8.3GB** | Well under 10GB cap |

### Memory Protection Techniques

| Technique | Where | Effect |
|-----------|-------|--------|
| SSD expert streaming via `pread()` | `infer.m` | 18GB model uses ~3GB RAM |
| `madvise(MADV_DONTNEED)` | `infer.m` | Evicts unused weight pages |
| `--kv-seq 2048` | `infer.m` | KV cache capped at ~200MB |
| Lazy ASR/TTS + idle unload | `asr.py`/`tts.py` | Saves ~1.55GB when idle |
| EMA affect in RAM only | `affect_types.py` | No per-event DB write |
| Sidecar LLM 0.8-4B | `mindd/` | Never contends with 35B |
| Context pack ≤600 tokens | `retrieval.py` | Bounded prompt size |
| Retention tier enforcement | `compressor.py` | Hot(7d)/Warm(90d)/Cold(forever) |
| Circuit breaker on TTFT drift | `server.py` | Pauses background work >300ms |
| `--mem-floor` in `infer.m` | Flash-MoE | Floor threshold for pressure relief |

### Response Brevity

GARY optimizes for **concise, useful responses**:
- System prompt enforces voice-first delivery: natural sentences, no unnecessary preamble
- Short responses complete faster → less TTS latency → better UX
- Reward system includes a **concision signal**: responses that are accepted by the user AND short get a higher quality score
- `MAX_TOKENS = 800` caps generation length

---

## 18. Evaluation Harness

Evaluation is a first-class subsystem, not an afterthought.

### Latency Metrics (Measured Continuously)

| Metric | Method | Target |
|--------|--------|--------|
| Barge-in stop latency | Timer from speech detect to silence | <100ms |
| Time to first transcript | Timer from VAD fire to ASR complete | <400ms |
| Time to first audio | Timer from ASR complete to first WAV sent | <600ms |
| TTFT (time to first token) | Timer from prompt send to first SSE delta | <200ms |

### Quality Metrics (Tracked in DB)

| Metric | Source |
|--------|--------|
| Initiative acceptance rate | `initiative_logs.outcome` |
| Proactive annoyance rate | `initiative_logs` where outcome = regretted |
| Retrieval hit rate | Retrieval audit in training pairs |
| False-memory rate | Claims retracted / total claims |
| Dream hallucination rate | Ideas from dreams that fail validation |
| Validator precision | Ideas validated / ideas tested |
| ASR correction rate | User corrections post-transcript |
| Fine-tune uplift vs baseline | Holdout eval before/after adapter |
| Deletion completeness | Artifacts removed on consent revocation |

### Acceptance Tests

Before any phase ships:
1. 273+ unit tests pass (current: 273 in 2.86s, 6 skipped)
2. Latency benchmarks within targets
3. No regression on existing functionality
4. Memory usage stays under 10GB cap

---

## 19. Tech Stack

| Component | Technology | Notes |
|-----------|-----------|-------|
| Reflex LLM | Qwen3.5-35B-A3B-4bit | Metal MoE, SSD streaming via `infer.m` |
| Mind Daemon LLM | Qwen 0.8B–4B (MLX) | Continuously improved via Flywheel |
| ASR | Qwen3-ASR-0.6B (MLX) | + runtime context hints |
| TTS | Kokoro-82M ONNX | Sub-100ms, sacred hot path |
| TTS clone | Qwen3-TTS (offline only) | Lab tier, consent-gated |
| VAD | Silero VAD (ONNX) | <2ms/chunk, MIT |
| DB | Postgres 16 + pgvector | Docker named volume, HNSW indexes |
| Embeddings | all-MiniLM-L6-v2 | 384d, chunked for >256 word pieces |
| Speaker ID | Resemblyzer (routing only) | 256d embedding, not authentication |
| Diarization | pyannote (offline only) | Lab/curation workflows |
| LLM adapt | MLX-LM LoRA/QLoRA | Apple Silicon native, rollback-ready |
| Framework | FastAPI + uvicorn | HTTPS, WebSocket, async |
| Frontend | Vanilla HTML/CSS/JS | No npm, no CDN, no framework |

### System Requirements

| Requirement | Notes |
|-------------|-------|
| macOS 13+ | Apple Silicon (M1/M2/M3/M4) required |
| Python 3.11+ | (3.14 compatible) |
| Docker | For Postgres + pgvector |
| 16GB+ RAM | 32GB+ recommended for full Mind Daemon |

---

## 20. Configuration Reference

### Environment Variables

| Variable | Default | Effect |
|----------|---------|--------|
| `GARY_PORT` | `7861` | HTTPS server port |
| `SSL_CERTFILE` | auto | TLS certificate path |
| `SSL_KEYFILE` | auto | TLS private key path |
| `GARY_DB_PASS` | — | Postgres password |
| `GARY_LOG_LEVEL` | `INFO` | Python logging level |
| `GARY_MIND_JSON` | unset | `1` → JSON mind pulses (schema v1); fallback to prose |
| `GARY_PERSIST_MIND` | unset | `1` → write parsed JSON pulses to `thoughts` table |
| `GARY_CONTEXT_PACK` | unset | `1` → **context compiler v2**: DB-backed slots + affect summary + pack_history_limit(10) |
| `GARY_MIND_REMOTE` | unset | `1` → calls mindd sidecar over HTTP instead of embedded llm_gate |
| `MINDD_PORT` | `7863` | Port for the mindd sidecar FastAPI server |
| `MINDD_URL` | `http://localhost:7863` | Full URL for the mindd sidecar |
| `MINDD_LLM_URL` | `http://localhost:8088/v1/chat/completions` | LLM endpoint used by the sidecar |
| `GARY_SESSION_LOG` | `1` | `1` → enable dual-tier JSONL session logging |
| `GARY_SESSION_LOG_DIR` | `GARY/logs/sessions/` | Custom output directory for session logs |
| `GARY_SESSION_LOG_MAX_MB` | `50` | Max file size before rotation |
| `GARY_SESSION_LOG_DETAILED` | `1` | `1` → enable the detailed (every-token) logging tier |
| `PYTHONPATH` | `GARY/` | Required for imports |

### Tuning Constants

| Location | Constant | Value | Meaning |
|----------|----------|-------|---------|
| `server.py` | `MAX_HISTORY_TURNS` | 20 (10 w/ pack) | Max conversation pairs, dynamic via `pack_history_limit()` |
| `vad.py` | `BARGEIN_PROB` | 0.70 | Speech probability threshold for barge-in |
| `vad.py` | `BARGEIN_FRAMES` | 2 | Consecutive frames above threshold → interrupt |
| `vad.py` | `_SILENCE_HANG_SEC` | 0.55 | Silence after speech before VAD fires |
| `vad.py` | `_MIN_SPEECH_SEC` | 0.15 | Min utterance duration |
| `vad.py` | `_RMS_FLOOR` | 0.004 | Below = silence |
| `llm.py` | `MAX_TOKENS` | 800 | LLM max output |
| `llm.py` | `TEMPERATURE` | 0.75 | Response creativity |
| `llm.py` | `CONNECT_TIMEOUT` | 3.0s | LLM connect timeout |
| `llm.py` | `READ_TIMEOUT` | 300.0s | LLM read timeout (v3.1: 5 min for large models) |
| `mind.py` | Phase triggers | 30s/120s/300s | Reflection/Brainstorm/Dream thresholds |
| `mind.py` | Phase cooldowns | 15s/30s/60s | Minimum delay between pulses |
| `mind.py` | `MIN_IDLE_FOR_THOUGHT` | 30.0 | Minimum idle seconds before any thought |
| `tts.py` | `_VOICE` | `am_adam` | Default TTS voice |
| `processor.js` | `CHUNK_SZ` | 1280 | AudioWorklet chunk (80ms, v3.1) |
| `server.py` | `IS_SPEAKING_TIMEOUT` | 30.0 | v3.2: hard cap seconds before `is_speaking` failsafe reset |
| `server.py` | `IDLE_TIMEOUT_SEC` | 300 | Unload ASR/TTS after 5 min idle (saves ~1.55GB) |

---

## 21. Security & TLS

- **HTTPS required**: `getUserMedia()` needs Secure Context for non-localhost
- **TLS provisioning**: Reuse sibling certs → `mkcert` → Python `cryptography` → `openssl` fallback
- **No auth**: Single-user, local network only. Do not expose to internet.
- **XSS prevention**: `renderMarkdown()` HTML-escapes before processing
- **Voice is NOT authentication**: Speaker embeddings route context, never authorize actions
- **Biometric governance**: Voice data under 7-scope consent with independent revocation

---

## 22. Known Limitations

1. **Single-file server**: `server.py` handles everything. Should split as it grows.
2. **Global TTS voice**: Module-level global. Multi-user needs per-connection voice.
3. **No ASR concurrency**: `max_workers=1` — acceptable for single-user.
4. **Partial ASR on MLX**: Streaming ASR currently requires vLLM backend. MLX path is R&D.
5. **TTS cloning on Mac**: Qwen3-TTS fine-tuning is CUDA-oriented. Lab tier only.
6. **openWakeWord licensing**: Code is Apache-2.0 but pretrained models are CC BY-NC-SA 4.0.
7. **Echo cancellation**: v3.1 uses `is_speaking` + `tts_finished` server-side gating + browser `echoCancellation: true`. No hardware AEC. May need threshold tuning without headphones.
8. **History in-memory**: Lost on restart (addressed by Memory Spine persistence).
9. **TurnSupervisor not integrated**: The planned `TurnSupervisor` class is designed but not wired into `server.py`. Turn control is currently handled by the simpler `turn_epoch` + `is_speaking` + `tts_finished` protocol.

---

## 23. Extension Guide

### Adding a New TTS Engine
1. Add `_sync_synthesize_myengine()` in `pipeline/tts.py`
2. Add engine flag controlled by env var
3. Dispatch in `_sync_synthesize()`

### Adding Tool Use
1. Detect `tool_calls` in SSE response in `pipeline/llm.py`
2. Handle in `_generate_and_speak()`: dispatch → await → re-call with result

### Replacing the LLM Backend
The client speaks OpenAI Chat Completions SSE. Change `LLM_URL`:
- `http://localhost:11434/v1/chat/completions` — Ollama
- `https://api.openai.com/v1/chat/completions` — OpenAI (add auth header)
- `http://localhost:1234/v1/chat/completions` — LM Studio

---

## 24. Build Phases

### Phase 0: Reflex Hardening ✅ (73 tests)
Spectral VAD, parallel TTS queue, ASR context hints, affect types, behavior policies, typed events, latency benchmarks.

### Phase 1: Memory Spine ✅ (15 tests)
Docker Postgres + pgvector, 10-table schema, asyncpg pool, event writer, fusion retrieval.

### Phase 1.5: Feedback Revisions ✅ (25 new tests → 113 total)
12-table schema (claims, initiative_logs, training_buffer), local append spool, mode-specific retrieval, spool-backed event writer, retention tiers, granular consent.

### Phase 2: Mind Daemon + mindd Sidecar ✅ (31 v3 tests → 273 total)
Phase triggers 30s/120s/300s, cooldowns 15s/30s/60s, `MIN_IDLE_FOR_THOUGHT=30.0`, removed "Be proactive" from system prompt. `apps/mindd/` sidecar with FastAPI `/pulse` + `/health`, client-disconnect detection, `GARY_MIND_REMOTE=1` flag, raced pulse with `asyncio.wait`, `_same_llm_origin()` warning, `mind_aborted` tracking.

### Phase 2b: Context Pack v2 + Schema v6.0 ✅
DB-backed retrieval slots (active_loops, top_claims, top_question), affect summary injection, `pack_history_limit()` (20→10), retrieval audit logging, 20-table schema.

### Phase B: Deep Cognition Stack ✅
Question Ledger (`core/question_ledger.py`), Commitment Tracker (`core/commitments.py`), Eval Harness (`core/eval_harness.py`), Rumination Governor (`core/rumination_governor.py`), Prompt Stack v2 (9 cognitive lane prompts in `core/prompts/*.txt`).

### Phase 2c: Bug Fixes ✅
`llm_gate` timeout (3×2s + forced mind yield), think-token logging in bright green (#00ff88), mind-token logging in hot pink (#ff69b4), filler audio pre-LLM.

### Phase 2d: Audio/Turn Control v3.1 ✅
- **Turn-epoch audio envelopes**: `audio_start`/`stop_audio` with monotonic epoch counter
- **`tts_finished` signaling**: Browser → server sync for exact audio completion
- **Pre-baked greeting WAV**: Instant playback from `static/audio/greeting.wav`
- **Click-to-activate overlay**: Guarantees browser AudioContext unlocked before audio
- **VAD echo protection**: `vad.push()` blocked while `is_speaking or is_active`
- **Deferred mic capture**: Mic starts only after greeting finishes playing
- **LLM read timeout**: 60s → 300s for massive local models
- **Docker/Podman dual support**: Startup scripts auto-detect container runtime
- **AudioWorklet v3.1**: 80ms chunks (was 160ms), onset detector, EMA noise floor
- **Mind initiative pipeline**: `[INITIATIVE: ...]` → `handle_utterance(text_override=...)`

### Phase 2e: Turn Classification + Session Logging + Failsafes (v3.2) ✅
- **Turn Classifier**: `pipeline/turn_classifier.py` — SNAP/LAYERED/DEEP classification (<1ms, deterministic)
- **TurnSupervisor**: `pipeline/turn_supervisor.py` — designed epoch/floor/preemption class (not yet wired)
- **Session Logger**: `core/session_logger.py` — dual-tier async JSONL (detailed + condensed)
- **Persistent Log Writer**: `core/log_writer.py` — 3 append-only log files (conversation, mind_stream, websocket)
- **Log Viewer**: `/logs/` HTML page with live-tailing via byte-offset polling
- **`is_speaking` failsafe**: 30s hard timeout resets stuck flag (v3.2)
- **Memory status endpoint**: `/api/memory_status` (ASR/TTS load state + free RAM)
- **Session log download**: `/api/logs/latest/{type}` REST endpoint
- **Structured Mind Pulse**: `core/mind_pulse.py` (JSON v1 schema, `GARY_MIND_JSON=1`)
- **Mind persistence**: `memory/mind_persist.py` writes parsed pulses to `thoughts` table (`GARY_PERSIST_MIND=1`)
- **Health broadcast**: `broadcast_health()` pushes model/RAM status to all connected WebSockets

### Phase 3: Initiative & Shadow Mode (Next)
Score formula, shadow mode logging, presence confidence, social budget, WebSocket proactive push.

### Phase 4: Dreams & Validation
Temperature curve, snap-back on speech, Dreamer → Scientist → Archivist pipeline, counterfactual rehearsal.

### Phase 5: Learning Lab
Training manifest curation, LoRA fine-tune on Apple Silicon, eval gates, rollback, biometric consent isolation.

---

## Feedback Synthesis: What 8 Reviewers Said

All 8 independent reviewers scored the architecture between **86-96/100**. Here is what we adopted, what we deferred, and why.

### Adopted (Already Implemented)

| Feedback | Implementation |
|----------|---------------|
| Claims ledger (epistemic truth) | `claims` table with contradiction/supersession edges |
| Local append spool (crash-safe) | `memory/spool.py` — fsync'd JSONL |
| Shadow mode for initiative | `initiative_logs` table + shadow-only outcome tracking |
| Truth-gated rewards | `rewards.truth_gated` field, lexicographic ordering |
| Appraisal layer (not naive mapping) | Interruption ≠ always rejection |
| Anti-rumination governor | `repeat_similarity × negative_affect × (1 - new_evidence)` |
| Mode-specific retrieval | 4 profiles: factual/relational/reflection/dreaming |
| Process isolation | 4 OS-level processes with circuit breaker |
| Identity kernel | Versioned, immutable core values |
| Retention tiers | Hot(7d)/Warm(90d)/Cold(forever) |
| Granular biometric consent | 7 independent scopes |
| Pulse scheduler (not continuous) | Microtrace/reflection/brainstorm/dream/consolidation |
| Relational Safety Governor | Internal affect ≠ outward expression |
| Causality fields | `session_seq`, `turn_id`, `parent_id` on events |

### Adopted in Design (Build in Later Phases)

| Feedback | Where |
|----------|-------|
| Social proactivity budget | Phase 4 (`initiative.py`) |
| Presence confidence model | Phase 4 (`presence.py`) |
| Counterfactual rehearsal | Phase 5/6 (`counterfactual.py`) |
| Response concision reward | Phase 3 (quality_signal includes length factor) |
| Eval harness as first-class | Phase 2+ (continuous latency + quality metrics) |
| Resource broker for Mind Daemon | Phase 3 (`scheduler.py`) |

### Consciously Deferred

| Suggestion | Why |
|------------|-----|
| PostgreSQL 18 | PG16 has better extension ecosystem stability today |
| `halfvec` for embeddings | Premature optimization — 384d is already small |
| Full experience graph DB | Too complex for Phase 1; claims + edges sufficient |
| Multi-user voice routing | Single-user Mac system. Add if needed. |
| FLAC at rest | Minor optimization. WAV simplicity wins for now. |

---

*End of Architecture Bible v3.2*

**Test status**: 19 test files in `testing/` covering all phases. Run with `python -m pytest testing/ -q`.

---

## 25. Session Logging & Persistent Logs

### 25.1 Session Logger (`core/session_logger.py`)

Dual-tier async JSONL session logger. Opt-in via `GARY_SESSION_LOG=1` (default: **on**).

**Two tiers per session:**

| Tier | File Pattern | Contents | Purpose |
|------|-------------|----------|---------|
| **Detailed** | `detailed_<session>.jsonl` | Every token, VAD prob, think block, timing | Engineering replay |
| **Condensed** | `condensed_<session>.jsonl` | User text, agent replies, timestamps, key timings | Study-grade / audit |

**Design:**
- `log()` and `log_condensed()` are synchronous, non-blocking (push to `asyncio.Queue`)
- Background `_writer_task` drains queue in batches (up to 64 events / 200ms)
- File rotation at `GARY_SESSION_LOG_MAX_MB` (default: 50MB)
- All detailed events include `ts`, `ts_mono` (monotonic delta from session start), `session_id`, `turn`, `event`, `source`, `data`
- Condensed events include `ts` (ISO 8601 UTC), `session_id`, `turn`, `actor`, `action`, `text`, `meta`

**Configuration:**

| Variable | Default | Effect |
|----------|---------|--------|
| `GARY_SESSION_LOG` | `1` | Enable/disable session logging |
| `GARY_SESSION_LOG_DIR` | `GARY/logs/sessions/` | Custom log directory |
| `GARY_SESSION_LOG_MAX_MB` | `50` | Max file size before rotation |
| `GARY_SESSION_LOG_DETAILED` | `1` | Enable/disable the detailed tier |

### 25.2 Persistent Log Writer (`core/log_writer.py`)

Thread-safe append-only log writer for three persistent files in `GARY/logs/`:

| File | Contents |
|------|----------|
| `conversation.log` | User / GARY dialogue (timestamped lines) |
| `mind_stream.log` | Internal monologue with phase labels |
| `websocket.log` | All discrete pipeline events |

File handles stay open for the process lifetime. A `threading.Lock` guards writes so the async mind-loop and WebSocket handler can call `append()` concurrently without interleaving.

**REST access**: The `/logs/` HTML page polls `/logs/api/{name}` every 2 seconds with byte-offset streaming for live tailing. Supports `conversation`, `mind_stream`, and `websocket` log names.

---

## 26. Turn Classifier

**File**: `pipeline/turn_classifier.py`

Zero-latency deterministic turn classifier. Runs after ASR, guaranteed <1ms, no I/O, no model calls.

### Turn Modes

| Mode | Description | Token Budget | Context Tier |
|------|-------------|-------------|---------------|
| **SNAP** | Greetings, yes/no, short factual (≤5 words) | ≤80 tokens | Micro |
| **LAYERED** | Default — everything else | ≤400 tokens | Standard |
| **DEEP** | Code, multi-step reasoning, explicit depth markers | ≤800 tokens | Full |

### Classification Logic

1. **SNAP exact match**: ~45 common phrases (`yes`, `no`, `thanks`, `hello`, `stop`, etc.)
2. **SNAP pattern match**: Regex for `what's the time`, `how are you`, `tell me a joke`, etc.
3. **SNAP word count**: ≤5 words without depth keywords → SNAP
4. **DEEP keyword match**: Regex for `explain`, `implement`, `code`, `step by step`, long how/why questions, etc.
5. **DEEP word count**: ≥40 words → DEEP
6. **Default**: LAYERED

### Integration

After ASR transcription, `server.py` calls `classify_turn(text)` and:
- Sends `{type: "turn_mode", mode: "snap"|"layered"|"deep"}` to the browser
- Logs the mode to the session logger
- Skips filler audio for SNAP turns (assumed fast enough without it)
- Stores the mode in `current_turn_mode` per-connection state

### TurnSupervisor (`pipeline/turn_supervisor.py`)

A `TurnSupervisor` class is **designed but not yet wired into `server.py`**. It encapsulates epoch management, floor state (`idle`/`thinking`/`speaking`), queue draining, and preemption into a single object. The current server uses inline `turn_epoch` + `is_speaking` logic instead. Migration target for future cleanup.

---

## 27. Structured Mind Pulses (JSON v1)

**File**: `core/mind_pulse.py`

When `GARY_MIND_JSON=1`, mind prompts request structured JSON output instead of free-form prose. The schema is versioned:

### MindPulse v1 Schema

```json
{
  "schema_version": 1,
  "inner_voice": ["1-3 short private lines"],
  "frames": [
    {
      "kind": "question|insight|repair|followup|hypothesis|experiment|boundary|other",
      "text": "...",
      "salience": 0.0
    }
  ],
  "initiative_candidate": null
}
```

### Dataclasses

| Class | Fields | Purpose |
|-------|--------|---------|
| `MindPulse` | `schema_version`, `inner_voice`, `frames`, `initiative_candidate` | Top-level pulse container |
| `ThoughtFrame` | `kind`, `text`, `salience` | Typed cognitive unit |
| `InitiativeCandidate` | `should_surface`, `draft`, `reason_code` | Structured initiative proposal |

### Processing Pipeline

1. **Parse**: `parse_mind_pulse_json(raw)` strips markdown fences, validates schema version, coerces types
2. **Display**: `format_mind_pulse_display(pulse)` renders inner_voice + frames for dedup/history/Mind panel
3. **Score**: `score_mind_pulse(pulse, phase)` computes 0–1 salience from frame count, salience values, inner_voice length, initiative presence, and phase bonuses
4. **Persist**: When `GARY_PERSIST_MIND=1`, `memory/mind_persist.py` inserts the full JSON pulse into the `thoughts` table with lane=`structured_json`

The legacy prose path (`[INITIATIVE: ...]` regex) remains as fallback when JSON parsing fails or `GARY_MIND_JSON` is not set.
