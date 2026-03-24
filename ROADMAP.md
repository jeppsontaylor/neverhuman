# Roadmap

Status: 🟢 Done · 🟡 In Progress · ⚪ Planned · 🔬 Research

## v0.1 — Foundation (current)

- 🟢 Voice pipeline: ASR → LLM → TTS, < 400ms
- 🟢 Qwen3.5-35B-A3B MoE brain via flash-moe (Metal)
- 🟢 Kokoro-82M TTS, 54 voices
- 🟢 Spectral VAD with barge-in detection
- 🟢 Permanent memory: Postgres 16 + pgvector, 21 tables
- 🟢 Context Pack v2 (DB-backed slots: claims, open loops, questions)
- 🟢 Background cognition (mind daemon, pulse scheduler)
- 🟢 13-dimension affect vector + appraisal layer
- 🟢 Humanity slider (0.0 → 1.0)
- 🟢 Session logging (dual-tier JSONL)
- 🟢 One-command installer (`bash install.sh`)
- 🟢 First-run model setup wizard

## v0.2 — Stability + Polish

- 🟡 `apps/mindd/` — isolated mind daemon process (separate from server.py)
- 🟡 `apps/trainerd/` — offline learning lab
- ⚪ Silero VAD v5 (upgrade from spectral VAD)
- ⚪ Settings panel: voice, humanity slider, model selector in-app
- ⚪ Mobile companion app (iOS, SwiftUI) — view memories, conversations
- ⚪ Windows + Linux support (non-Metal inference backend)

## v0.3 — Initiative

- ⚪ Shadow mode: GARY logs what it would say but stays silent (2-week calibration)
- ⚪ Initiative scoring: excitement × open_loop_urgency × validated_idea_readiness
- ⚪ Social budget: user-adaptive proactive speech rate
- ⚪ Presence confidence model

## v0.4 — Learning

- ⚪ Opt-in LoRA fine-tuning pipeline
- ⚪ Counterfactual rehearsal (what_said → what_should_have_said)
- ⚪ Eval gates before adapter deployment
- ⚪ Training pair export + community model sharing

## v1.0 — Connectors

- ⚪ Calendar / reminders
- ⚪ Browser integration (summarize, remember, annotate)
- ⚪ File system awareness (project context)
- 🔬 Biometric input (heart rate → emotional calibration)

## Long-term research

- 🔬 Multi-user household awareness
- 🔬 Cross-device memory sync (local mesh, no cloud)
- 🔬 Neural interface compatibility research
