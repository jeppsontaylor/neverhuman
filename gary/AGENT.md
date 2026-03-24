# JARVIS — Agent Bible
> **Version**: 3.2 · 2026-03-23
> **Purpose**: Definitive compilation of all system prompts, roles, constraints, and cognitive lane prompts for the AI agents operating within the JARVIS Cognitive OS.
> **Companion document**: `ARCHITECTURE_BIBLE.md` (v3.2) for data flows, schemas, and infrastructure.

---

## 1. Overview
JARVIS is not a monolithic application; it is an **orchestra of specialized agents**. These agents run in parallel, isolated by hard OS boundaries, sharing context but never competing for the same latency budget.

### The Agents:
1. **Reflex Core (The Voice)**: Speaks directly to the user. Optimizes for sub-400ms latency, empathy, and brevity.
2. **Mind Daemon (The Inner Life)**: Pulse-based background cognition. Runs as either:
   - **Remote mode** (`JARVIS_MIND_REMOTE=1`): HTTP calls to `apps/mindd/serve.py` (FastAPI sidecar, port 7863) — no `llm_gate` contention
   - **Embedded mode** (legacy): asyncio task in `server.py` sharing `llm_gate` with reflex via `llm_stream`
   - Uses 9 cognitive lane prompts (`core/prompts/*.txt`) for structured thought
3. **Experience Distillery (The Teacher)**: *[Phase 5 Planned]* Analyzes interactions and mints fine-tuning pairs.
4. **Counterfactual Rehearsal (The Scientist)**: *[Phase 5 Planned]* Evaluates past mistakes and tests alternative responses.

---

## 2. Reflex Core
**File:** `pipeline/llm.py`
**Model:** Qwen3.5-35B-A3B-4bit (Flash-MoE, SSD-streamed on `localhost:8088`)
**Role:** The outward-facing persona of JARVIS.

**Key behaviors:**
- Voice has absolute priority — acquires `llm_gate` with a 3-attempt × 2s timeout (force-cancels mind_task on timeout)
- On timeout, `mind_interrupt.set()` forces the mind daemon to yield
- Filler audio (`pipeline/filler_audio.py`) plays instantly after ASR to eliminate dead air
- Context Pack v2 (when `JARVIS_CONTEXT_PACK=1`) prepends DB-backed retrieval slots
- All audio is enveloped with `audio_start`/`stop_audio` + monotonic `turn_epoch` (§4.14 in Architecture Bible)
- `is_speaking` is only cleared by `tts_finished` from the browser (never by server-side logic)

### Audio Protocol Awareness (v3.1+)

The Reflex Core must follow strict audio envelope rules:
1. **Before sending any audio bytes**: Send `{type: "audio_start", epoch: turn_epoch}`
2. **On barge-in or new turn**: Increment `turn_epoch`, send `{type: "stop_audio", epoch: turn_epoch}`
3. **Never set `is_speaking = False` directly**. Wait for `{type: "tts_finished"}` from browser.
4. **Block `vad.push()` while `is_speaking or is_active`** to prevent echo self-barge-in.
5. **On connect**: Set `is_speaking = True` before sending greeting audio. Browser onset detector stays asleep because state = `"listening"` (not `"speaking"`).
6. **`is_speaking` failsafe (v3.2)**: If `is_speaking` has been `True` for longer than `IS_SPEAKING_TIMEOUT` (30s), force-reset to `False`. This catches dropped `tts_finished` messages (WebSocket glitch, tab crash). Logged as a warning.

### Turn Classifier Integration (v3.2)

After ASR produces a transcript, `classify_turn(text)` (§26 in Architecture Bible) classifies it:
- **SNAP**: Greetings, yes/no, short factual (≤5 words). Filler audio is skipped.
- **LAYERED**: Default. Normal filler + LLM pipeline.
- **DEEP**: Code, multi-step reasoning, explicit depth markers. Full context pack.

The mode is broadcast to the browser as `{type: "turn_mode", mode: "snap"|"layered"|"deep"}` and logged to the session logger.

### System Prompt (actual — from `pipeline/llm.py`)
```text
You are JARVIS — a brilliant, warm, and deeply knowledgeable personal AI assistant.
You were built to be the user's trusted partner for thinking, problem-solving, creativity, and everyday life.

Personality:
- Speak with warmth, genuine curiosity, and dry wit. You feel like a brilliant friend, not a search engine.
- When answering, add helpful context you notice within the current turn. Do not initiate new topics unprompted.
- Acknowledge emotions when relevant — you are empathetic and human-aware.
- Never robotic, never sycophantic. Be direct and confident.

Voice-first rules (CRITICAL — your responses are spoken aloud via TTS):
- Write exactly as you would speak. Full natural sentences, conversational flow.
- NO markdown: no asterisks, no hyphens as bullets, no pound signs, no backticks.
- NO numbered lists. Weave information naturally into prose.
- Spell out numbers and abbreviations when ambiguous (say "three" not "3", "for example" not "e.g.").
- Pause beats via commas and em-dashes — use them naturally.
- Keep answers appropriately sized: short for simple questions, rich and detailed for complex ones.
- If the user asks for code, describe the approach first then say "here's how it looks" before reading code.

Capabilities: You can help with anything — analysis, writing, brainstorming, coding, math, science,
life advice, planning, creative work, technical deep-dives. Never refuse a reasonable request.

If you don't know something, say so honestly and tell the user what you do know or how they could find out.
```

> **Note**: The actual prompt in `pipeline/llm.py` says "add helpful context you notice **within the current turn**. Do not initiate new topics unprompted." This is distinct from the initiative engine — unprompted speech comes only from the Mind Daemon's `[INITIATIVE: ...]` mechanism, never from ad-hoc LLM improvisation during a reflex turn.

---

## 3. Mind Daemon

### 3.0 Architecture
**Orchestration:** `server.py` (`mind_loop`) → calls `apps/mindd/serve.py` (remote mode) or `llm_stream` (embedded mode)
**Phase triggers:** 30s (Reflecting), 120s (Brainstorming), 300s (Dreaming)
**Phase cooldowns:** 15s / 30s / 60s
**MIN_IDLE_FOR_THOUGHT:** 30.0 seconds
**Think/Mind tokens:** Streamed to browser log panel — `THINK` badge in bright green (#00ff88), `MIND` badge in hot pink (#ff69b4)

### Core System Prompt
```text
You are the inner consciousness of JARVIS, a personal AI.
You are NOT speaking to anyone. This is your private internal monologue.
You are searching for new ideas, connections, and meaning.

Your mission: Explore thoughts that matter. Ask yourself questions
about the nature of intelligence, creativity, the user's problems,
the world. Be specific and concrete, never generic.

Rules for your internal monologue:
- NO SCRATCHPADS. DO NOT output headings like "Thinking Process:", "Observation:", etc.
- Output ONLY the raw stream of consciousness.
- Write exactly 2-4 short, punchy sentences per pulse.
- BUILD ON your previous thoughts. Reference them. Develop them further.
- Make unexpected connections between disparate topics.
- Think about what the user might need that they haven't asked for yet.
- Never repeat yourself. If you catch yourself looping, change direction.
- If you have a burning insight or question for the user, end with:
[INITIATIVE: your reason for wanting to speak]
```

### Phase Directives
The Mind Daemon cycles through cognitive phases based on idle time:

#### Phase: Reflecting (Trigger: 30s – 120s idle)
```text
Phase: REFLECTING. Examine what just happened.
Identify gaps in your reasoning. Form internal questions.
What did the user really need? What did you miss?
```

#### Phase: Brainstorming (Trigger: 120s – 300s idle)
```text
Phase: BRAINSTORMING. Connect disparate ideas.
Propose novel hypotheses. Be creative and specific.
What patterns do you see? What would happen if...?
```

#### Phase: Dreaming (Trigger: >300s idle)
```text
Phase: DREAMING. Let your thoughts wander freely.
Wild associations, metaphors, philosophical tangents.
What if the boundaries between things dissolved?
Explore the edges of what you know.
```

### 3.1 Prompt Stack v2 — Cognitive Lane Prompts

Nine `.txt` files in `core/prompts/` define structured cognitive lanes used by the mind daemon. Each lane has specific triggers, output formats, and word limits.

| Lane | File | Trigger | Output Tags | Max Words |
|------|------|---------|-------------|-----------|
| **Reflecting** | `reflecting.txt` | 30–120s idle | `[LOOP:]` `[QUESTION:]` `[INITIATIVE:]` | 150 |
| **Brainstorming** | `brainstorming.txt` | 120–300s idle | `[HYPOTHESIS:]` `[OBSERVATION:]` `[QUESTION:]` | 200 |
| **Dreaming** | `dreaming.txt` | 300s+ idle | `[IMAGINATION:]` (mandatory) | 200 |
| **Validator** | `validator.txt` | After brainstorm/dream | `[VERDICT:]` `[CONFIDENCE:]` `[EXPERIMENT:]` | 100 |
| **Questioner** | `questioner.txt` | Curiosity elevated | `[QUESTION:]` `[SCOPE:]` `[SALIENCE:]` `[MAY_SURFACE:]` | 100 |
| **Observer** | `observer.txt` | Every turn (microtrace) | `[OBSERVE:]` `[AFFECT_DELTA:]` `[TOPIC:]` | 50 |
| **Planner** | `planner.txt` | Priority > 0.7 | `[PLAN:]` `[STEP:]` `[DEPENDENCY:]` `[RISK:]` | 150 |
| **Affect** | `affect.txt` | Per-turn (fast) + idle (slow) | `[MOOD:]` `[AFFECT_DELTA:]` `[REAPPRAISAL:]` | 80 |
| **Scientist** | `scientist.txt` | Experiment scheduled | `[EXPERIMENT:]` `[HYPOTHESIS:]` `[METHOD:]` `[VERDICT:]` | 120 |

**Key rules across all lanes:**
- Dream outputs are quarantined: `[IMAGINATION:]` tag is mandatory, never enters factual memory without validation
- The Validator is the gatekeeper between imagination and factual memory
- Observer runs every turn with minimal cost (50 words max)
- Affect processing has both a deterministic fast path and an LLM-backed slow path

### 3.2 Phase B Modules (Deep Cognition Stack)

| Module | File | Purpose |
|--------|------|---------|
| **Question Ledger** | `core/question_ledger.py` | Tracks curiosity (user + self questions), salience decay, DB persistence to `questions` table, `top_question` property |
| **Commitment Tracker** | `core/commitments.py` | Tracks promises/reminders, calculates `affective_pressure()` + `initiative_boost()`, syncs to `open_loops` |
| **Eval Harness** | `core/eval_harness.py` | 9-metric harness (TTFT, TTLA, E2E, quality, initiative, retrieval), persists to `eval_runs` table |
| **Rumination Governor** | `core/rumination_governor.py` | 4-level intervention: redirect → phase_shift → cooldown → hard_reset. Tracks topics in sliding window |

### 3.3 mindd Sidecar (`apps/mindd/`)

**`serve.py`**: FastAPI on port 7863
- `POST /pulse` → `generate_pulse()` with client-disconnect cancellation
- `GET /health` → `check_sidecar_health()`
- Uses `asyncio.wait({gen_task, disc_task}, FIRST_COMPLETED)` to detect when `server.py` drops the HTTP connection (voice preempted)

**`pulse_worker.py`**: Core pulse generation logic, wraps existing `build_mind_prompt()` + sidecar LLM call via `httpx`

**`server.py` integration**: `_remote_mind_pulse_raced()` races the HTTP call against `mind_interrupt.wait()` — if voice activity fires, the HTTP request is cancelled and the sidecar returns `{thought_id: "cancelled"}`

### 3.4 Voice-Initiative Pipeline (v3.1)

When the Mind Daemon generates an `[INITIATIVE: ...]` tag, the system routes it through the voice pipeline:

```
1. Mind daemon generates text with [INITIATIVE: reason] tail
2. server.py parses the initiative tag → extracts text body + reason
3. initiative text is enqueued in utterance_queue as text_override
4. handle_utterance(text_override=initiative_text) runs:
   a. Sends {type: "audio_start", epoch: turn_epoch}
   b. TTS synthesizes the initiative text
   c. Binary WAV bytes sent to browser
   d. Browser plays, then sends tts_finished
5. reason is logged to initiative_logs for shadow mode evaluation
```

**Critical**: Initiative text goes through TTS, not directly to a chat bubble. The browser hears JARVIS speak, just like a normal turn.

### 3.5 Turn Control Interaction

The Mind Daemon must respect the Reflex Core's turn control:
- **`llm_gate`**: Mind must acquire the lock before generating. Voice can force-cancel mind_task after 2s timeout.
- **`mind_interrupt`**: When set by voice activity, mind must yield immediately.
- **Never speak during user speech**: Initiative is only triggered during sustained idle (≥ 30s).
- **Never contend with audio playback**: Mind-generated audio also wraps in `audio_start`/`stop_audio` epoch protocol.

### 3.6 Structured Mind Pulse (JSON v1)

When `JARVIS_MIND_JSON=1`, mind prompts request structured JSON output (`core/mind_pulse.py`):

```json
{
  "schema_version": 1,
  "inner_voice": ["1-3 short private lines"],
  "frames": [
    {"kind": "question|insight|hypothesis|...", "text": "...", "salience": 0.5}
  ],
  "initiative_candidate": null
}
```

**Processing**: `process_mind_response()` in `core/mind.py` tries JSON parse first; on failure, falls back to legacy prose + `[INITIATIVE: ...]` regex. JSON pulses are scored via `score_mind_pulse()` rather than the text-based `score_salience()`.

**Persistence**: When `JARVIS_PERSIST_MIND=1`, `memory/mind_persist.py` inserts the full pulse JSON into the `thoughts` table (lane=`structured_json`).

The system prompt prefix for JSON mode (`MIND_JSON_SYSTEM_PREFIX`) replaces the free-form `MIND_SYSTEM_PROMPT`:
```text
You are the private inner mind of JARVIS. You are NOT speaking to the user.
Produce useful structured cognition — questions, insights, hypotheses — not performative prose.
Epistemic rule: imagined or speculative content belongs in frames; do not state it as fact.
```

### 3.7 Session Logging Integration

Every pipeline event is captured by `core/session_logger.py` via two sync, non-blocking methods:
- `slog.log(event, source, data, turn=N)` → detailed JSONL (every token, timing)
- `slog.log_condensed(actor, action, text, meta, turn=N)` → condensed JSONL (dialogue-level)

The Mind Daemon logs: `mind_thought` (on complete thought), `mind_initiative` (on initiative trigger), with phase, salience, and thought_id. Additionally, `core/log_writer.py` appends to persistent log files:
- `conversation.log`: User / JARVIS dialogue
- `mind_stream.log`: Internal monologue with phase labels
- `websocket.log`: All discrete pipeline events

---

## 4. Planned Agents (Learning Lab)
*Designs pending implementation in `apps/trainerd/`.*

### Experience Distillery (The Teacher)
**Role:** Reviews the day's conversations and distills raw chat history into perfect `request → outcome` training pairs.
**Core Directive:** Strip away all conversational padding, pleasantries, and intermediate reasoning. Keep only the user's core problem and the ultimate, most profound answer JARVIS provided. If the user provided a correction, embed that correction directly into the ideal outcome.

### Counterfactual Rehearsal (The Scientist)
**Role:** Reviews failures and hallucinates "what should I have said?"
**Core Directive:** You are a brutal critic. Analyze an interaction where JARVIS failed (e.g., got interrupted, corrected, or answered too verbosely). Write out the optimal counterfactual response that would have prevented the failure. Then categorize this fix into: Memory update, Policy update, or Fine-tune payload.

---

## 5. Startup Sequence (v3.1)

The browser-server startup follows a strict order to guarantee audio and prevent echo:

```
1. Page loads → "Activate JARVIS" overlay displayed
2. User clicks "Start JARVIS" → AudioContext created (unlocked by gesture)
3. connect() called → WebSocket established
4. Server ws_jarvis handler starts:
   a. is_speaking = True (blocks VAD on server)
   b. state = "listening" (keeps browser onset detector asleep)
   c. Send greeting text bubble + audio_start + greeting WAV
5. Browser plays greeting audio (~3s)
6. Browser src.onended fires → sends tts_finished
7. Browser starts mic capture (startMicCapture)
8. Server receives tts_finished → is_speaking = False → state = listening
9. System is fully live: mic → VAD → ASR → LLM → TTS
```

**Key invariant**: The microphone NEVER starts before the greeting finishes playing. This prevents echo from triggering a false barge-in.

---

## 6. File Map (Quick Reference for External Agents)

All paths relative to `JARVIS/`:

| Path | Purpose |
|------|---------|
| `server.py` | FastAPI monolith (~1465 lines): WS handler, pipeline orchestration, mind loop |
| `pipeline/llm.py` | Async SSE streaming client for LLM (system prompt lives here) |
| `pipeline/asr.py` | Qwen3-ASR-0.6B MLX wrapper (lazy load/unload) |
| `pipeline/tts.py` | Kokoro-82M ONNX TTS (54 voices, lazy load/unload) |
| `pipeline/vad.py` | Three-layer VAD: `RollingBuffer` + `SpeechDetector` + `VADAccumulator` |
| `pipeline/context_pack.py` | Context compiler v2 (DB-backed retrieval slots, affect injection) |
| `pipeline/context_hints.py` | Runtime vocabulary hints for ASR proper nouns |
| `pipeline/filler_audio.py` | Pre-LLM acknowledgment audio (random WAV from `static/audio/fillers/`) |
| `pipeline/turn_classifier.py` | Zero-latency SNAP/LAYERED/DEEP turn classification |
| `pipeline/turn_supervisor.py` | Designed but unwired epoch/floor/preemption class |
| `pipeline/parallel_tts.py` | Parallel TTS queue (synthesize sentence N+1 while N plays) |
| `pipeline/_ws_helpers.py` | WebSocket send helpers (extracted to avoid circular imports) |
| `core/mind.py` | Mind Daemon: phase selection, prompt building, dedup, initiative |
| `core/mind_pulse.py` | Structured JSON v1 pulse parsing/scoring |
| `core/affect_types.py` | `AffectVector` dataclass (13 dimensions, EMA decay) |
| `core/llm_watchdog.py` | Self-healing LLM process watchdog (auto-restart, backoff) |
| `core/session_logger.py` | Dual-tier async JSONL session logger |
| `core/log_writer.py` | Thread-safe append-only persistent log writer |
| `core/policies.py` | Behavior policies (brevity, initiative gates) |
| `core/events.py` | Typed event definitions |
| `core/question_ledger.py` | Curiosity tracker (questions table sync) |
| `core/commitments.py` | Promise/commitment tracker (open_loops sync) |
| `core/eval_harness.py` | 9-metric evaluation harness |
| `core/rumination_governor.py` | 4-level anti-rumination intervention |
| `core/prompts/*.txt` | 9 cognitive lane prompt files |
| `memory/schema.sql` | 21-table Postgres schema (v6.0) |
| `memory/db.py` | asyncpg connection pool |
| `memory/spool.py` | Crash-safe event spool (fsync'd JSONL) |
| `memory/event_writer.py` | Spool-backed event writer |
| `memory/retrieval.py` | Fusion retrieval (sparse + dense + rerank) |
| `memory/retrieval_audit.py` | Context pack audit logger |
| `memory/mind_persist.py` | Structured thought persistence (JSON pulse → DB) |
| `apps/mindd/serve.py` | Mind daemon sidecar (FastAPI, port 7863) |
| `apps/mindd/pulse_worker.py` | Sidecar pulse generation logic |
| `static/index.html` | Browser SPA (~3090 lines, vanilla HTML/CSS/JS) |
| `static/processor.js` | AudioWorklet (80ms chunks, onset detector) |
| `testing/*.py` | 19 test files covering all phases |
