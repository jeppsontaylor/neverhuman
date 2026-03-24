# Architecture Overview

This document is a high-level entry point. For the full, deep technical reference, see [`gary/ARCHITECTURE.md`](gary/ARCHITECTURE.md).

---

## System summary

NeverHuman (GARY) is a **five-part cognitive operating system** for Apple Silicon:

| Component | What it does | Priority |
|---------|-------------|----------|
| **UI Layer** | Browser SPA and setup flow handling | High |
| **Reflex Core** | FastAPI orchestration and state handling | Highest |
| **Inference Runtime** | Local ASR, TTS, and Qwen LLM execution | Highest |
| **Memory Spine** | Postgres + pgvector durable storage | High |
| **Mind Daemon** | Background reflection, consolidation, and event processing | Low |

**Single inviolable rule**: when the user speaks, everything else yields instantly.

---

## Data flow

```
Browser (index.html)
   ↕ WebSocket /ws/gary (binary: PCM up, WAV down)
gary/server.py  (FastAPI + uvicorn, port 7861, HTTPS)
   ├── pipeline/vad.py       Spectral VAD, <0.5ms/chunk
   ├── pipeline/asr.py       Qwen3-ASR-0.6B (MLX, lazy load)
   ├── pipeline/llm.py       HTTP SSE → flash-moe (port 8088)
   └── pipeline/tts.py       Kokoro-82M (ONNX, 54 voices)
           ↓
flash-moe / infer            Qwen3.5-35B-A3B-4bit (Metal, SSD-streamed)
           ↓
memory/schema.sql            Postgres 16 + pgvector (Docker)
```

---

## Key numbers

| Metric | Value |
|--------|-------|
| Speech-end → first audio | < 400ms |
| LLM resident RAM | ~3-4GB (18GB on SSD) |
| Test suite | 159 tests, 0 failures |
| ASR model | 0.6B parameters |
| LLM | 35B MoE, 4-bit |
| TTS | 82M parameters |
| Memory tables | 21 tables, Schema v6.0 |

---

## Full reference

Everything — subsystem internals, protocol specs, database schema, config reference, security model — is in [`gary/ARCHITECTURE.md`](gary/ARCHITECTURE.md).
