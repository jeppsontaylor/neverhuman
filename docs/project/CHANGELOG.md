# Changelog

All notable changes to NeverHuman (GARY) are documented here.

Format: [Semantic Versioning](https://semver.org). Sections: `Added`, `Changed`, `Fixed`, `Removed`.

---

## [Unreleased]

### Added
- First-run model setup wizard (`gary/static/setup.html`) with SSE download progress
- `gary/pipeline/model_manager.py` — HF model catalog, cache detection, flash-moe detection
- Setup API routes: `/setup`, `/api/setup/status`, `/api/setup/models`, `/api/setup/download/<id>`
- `install.sh` — one-command installer (platform check, deps, Docker, TLS, flash-moe, setup wizard)
- `gary/.env.example` — documented configuration reference
- Apache 2.0 license, full OSS documentation suite

### Changed
- WebSocket endpoint renamed from `/ws/jarvis` → `/ws/gary`
- All environment variables renamed `JARVIS_*` → `GARY_*`
- Thread pool prefixes: `jarvis-asr` → `gary-asr`, `jarvis-tts` → `gary-tts`
- Session log role: `"jarvis"` → `"gary"`
- DB channel: `jarvis_events` → `gary_events` (db.py + schema.sql)
- Policy identifier: `jarvis_classic` → `gary_classic`
- `core/llm_watchdog.py` made fully portable: `GARY_INFER_BIN`, `GARY_INFER_MODEL`
- `pipeline/llm.py` system prompt moved to `core/prompts/system.txt`
- Docker compose: all container/volume/db names use `neverhuman`

### Removed
- `gary/fix_layout.py` (prototype utility)

---

## [0.1.0] — 2026-03-23 (pre-release internal)

### Added
- Real-time voice pipeline: spectral VAD → Qwen3-ASR-0.6B → flash-moe 35B → Kokoro-82M TTS
- Barge-in detection (server-side spectral + client-side onset)
- Turn-epoch audio envelope protocol (prevents stale audio contamination)
- Context Pack v2: DB-backed slots (claims, open_loops, questions)
- 159-test pytest suite
- Background mind daemon (pulse scheduler: microtrace, reflection, brainstorm, dream, consolidation)
- 13-dimension affect vector + appraisal layer + rumination governor
- Humanity slider (0.0–1.0)
- Postgres 16 + pgvector memory spine (21 tables, Schema v6.0)
- Local append spool (crash-safe fsync'd JSONL bridge)
- Session logger: dual-tier JSONL (detailed + condensed)
- Pre-baked greeting WAV (instant audio on WebSocket connect)
- LLM gate timeout (3 × 2s + force mind yield)
- `is_speaking` failsafe (30s hard cap)
- Turn classifier (SNAP/LAYERED/DEEP)
