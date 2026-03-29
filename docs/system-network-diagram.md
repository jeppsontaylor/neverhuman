# GARY Full System Network Diagram

This document maps the **working runtime components** in NeverHuman/GARY and how data, control, and persistence flow between them.

## 1) End-to-end runtime network (voice turn + cognition)

```mermaid
flowchart LR
    %% ============== Client Surface ==============
    subgraph CLIENT[Client Surface]
        UI["Browser SPA\nstatic/index.html"]
        AW["AudioWorklet\nstatic/processor.js\n16kHz mic chunks + onset detect"]
        UI --> AW
    end

    %% ============== Reflex Core ==============
    subgraph REFLEX[Process 1 — Reflex Core (gary/server.py)]
        WS["WebSocket /ws/gary\nJSON events + binary audio frames"]
        VAD["pipeline/vad.py\nSpeech detection + utterance segmentation"]
        TURN["pipeline/turn_classifier.py\nSNAP / LAYERED / DEEP"]
        ASR["pipeline/asr.py\nQwen3-ASR (MLX)"]
        HINTS["pipeline/context_hints.py\nASR vocabulary hints"]
        PACK["pipeline/context_pack.py\nContext Pack v2 compiler"]
        LLMCLI["pipeline/llm.py\nSSE client + reflex prompt"]
        SAN["pipeline/output_sanitizer.py\nSafety/format cleanup"]
        NORM["pipeline/tts_normalizer.py\nSpeech-friendly text normalization"]
        TTS["pipeline/tts.py\nKokoro ONNX synthesis"]
        PTTS["pipeline/parallel_tts.py\nSentence overlap synthesis queue"]
        FILL["pipeline/filler_audio.py\nInstant acknowledgement audio"]
        SUP["pipeline/turn_supervisor.py\nTurn orchestration hooks"]

        WS --> VAD --> ASR --> TURN
        ASR --> HINTS
        TURN --> PACK
        PACK --> LLMCLI --> SAN --> NORM --> TTS --> PTTS --> WS
        TURN --> FILL --> WS
        SUP --- WS
    end

    %% ============== Inference Runtime ==============
    subgraph INFER[Inference Runtime (local model services)]
        FLASH["flash-moe/infer.m\nQwen3.5-35B MoE\nOpenAI-compatible SSE endpoint"]
        LLMWD["core/llm_watchdog.py\nLLM liveness + auto-restart"]
    end

    %% ============== Memory Spine ==============
    subgraph MEMORY[Process 2 — Memory Spine]
        EVENTW["memory/event_writer.py\nAppend writer"]
        SPOOL["memory/spool.py\nCrash-safe local spool JSONL"]
        DB["memory/db.py\nasyncpg pool"]
        PG[("Postgres + pgvector\ncompose: gary/docker/compose.yml")]
        RET["memory/retrieval.py\nSparse+dense fusion retrieval"]
        RAUD["memory/retrieval_audit.py\nRetrieval trace + audits"]
        MPER["memory/mind_persist.py\nStructured thought persistence"]

        EVENTW --> SPOOL --> DB --> PG
        RET --> DB
        RAUD --> DB
        MPER --> DB
    end

    %% ============== Mind Daemon ==============
    subgraph MIND[Process 3 — Mind Daemon]
        MLOOP["core/mind.py\nPhase scheduler + initiative parse"]
        MPULSE["core/mind_pulse.py\nJSON pulse parsing/scoring"]
        AFFECT["core/affect_types.py\nAffective state vector"]
        POL["core/policies.py\nBehavior + initiative gating"]
        QLED["core/question_ledger.py\nQuestion salience tracking"]
        COMMIT["core/commitments.py\nOpen-loop commitments"]
        RUM["core/rumination_governor.py\nAnti-loop governor"]
        CHG["core/change_router.py\nChange routing"]
        PULSEAPI["apps/mindd/serve.py\nFastAPI sidecar :7863"]
        PWORK["apps/mindd/pulse_worker.py\nPulse generation worker"]
        PROMPTS["core/prompts/*.txt\n9 cognitive lane prompts"]

        MLOOP --> MPULSE
        MLOOP --> AFFECT
        MLOOP --> POL
        MLOOP --> QLED
        MLOOP --> COMMIT
        MLOOP --> RUM
        MLOOP --> CHG
        MLOOP --> PULSEAPI --> PWORK
        PWORK --> PROMPTS
    end

    %% ============== Observability & Evaluation ==============
    subgraph OBS[Observability / Evaluation]
        SLOG["core/session_logger.py\nDetailed + condensed JSONL"]
        LWR["core/log_writer.py\nconversation/mind/ws logs"]
        EVAL["core/eval_metrics.py + core/eval_harness.py\nLatency + quality metrics"]
        DRIFT["core/drift_audit.py\nBehavior drift checks"]
        RARB["core/resource_arbiter.py\nResource pressure arbitration"]
        CKPT["core/session_checkpoint.py\nState checkpoints"]
    end

    %% ============== External app services ==============
    subgraph APPS[Operational app services]
        ROUTERD["apps/routerd/serve.py\nRouting daemon endpoint"]
        FORGED["apps/forged/planner.py\nPlanning service"]
    end

    %% ============== Top-level connections ==============
    AW --> WS
    LLMCLI -- HTTP SSE --> FLASH
    LLMWD --> FLASH

    PACK --> RET
    MLOOP --> MPER
    MLOOP --> EVENTW

    REFLEX --> SLOG
    REFLEX --> LWR
    MIND --> SLOG
    MIND --> LWR
    OBS --> DB

    ROUTERD -. control plane .- REFLEX
    FORGED -. planning input .- MIND

    WS --> UI
```

## 2) Roles by subsystem

| Subsystem | Primary role | Critical interfaces |
|---|---|---|
| Client Surface | Captures microphone frames, renders chat, plays TTS audio, and emits lifecycle events (`tts_finished`, etc.). | `wss://.../ws/gary`, binary PCM up / WAV down. |
| Reflex Core | Real-time turn taking and response generation path with strict low-latency priority. | VAD/ASR/LLM/TTS pipeline, turn classifier, filler audio, interruption control. |
| Inference Runtime | Hosts local LLM inference process and watchdog controls. | OpenAI-compatible streaming completion endpoint. |
| Memory Spine | Durable storage, retrieval, and crash-safe event buffering. | Postgres+pgvector via `asyncpg`, spool append semantics. |
| Mind Daemon | Background cognition (reflecting/brainstorming/dreaming), initiative generation, salience and affect evolution. | Sidecar `/pulse` API, prompt lanes, persistence hooks. |
| Observability & Evaluation | Session logs, persistent logs, performance tracking, drift detection, and checkpointing. | JSONL logs, eval rows, DB-backed audits. |
| App Services | Specialized daemonized services for routing/planning in multi-process deployments. | `apps/routerd` and `apps/forged` control-plane style interactions. |

## 3) Protocol edges that define behavior

- **Realtime transport edge:** Browser `AudioWorklet` streams mic audio to `server.py` over WebSocket; server streams synthesized WAV back to browser.
- **Inference edge:** Reflex LLM client (`pipeline/llm.py`) calls Flash-MoE inference endpoint via streaming SSE.
- **Memory edge:** Retrieval and event writers access Postgres through `memory/db.py` and use spool fallback for resilience.
- **Cognition edge:** Mind loop calls sidecar (`apps/mindd/serve.py`) for pulse generation while maintaining preemption semantics with reflex priority.
- **Observability edge:** Reflex + mind publish structured events to session logger and persistent log writer for traceability and replay.

## 4) “Full power GARY” operating mode (what must all be alive)

1. Browser SPA + AudioWorklet session.
2. `gary/server.py` WebSocket loop and reflex pipeline.
3. Flash-MoE inference service (LLM endpoint reachable).
4. Postgres + pgvector with healthy `memory/db.py` pool.
5. Mind sidecar (`apps/mindd/serve.py`) with pulse worker and prompt lanes.
6. Session logging / persistent log writers for auditability.

If one of these is missing, GARY still may function partially, but “full power” behavior (voice + memory + reflective cognition + observability) is reduced.
