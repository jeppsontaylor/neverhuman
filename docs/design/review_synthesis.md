# Review Synthesis

> Distilled from feedback1–8 (architectural review notes). These are critiques and recommendations from independent reviews of the GARY system architecture.

## Critical findings across reviews

### 1. Turn-taking is the hardest unsolved problem

> "The core bug is this: speech start and speech end are being treated too similarly. Speech start should be fast, high-recall, interrupt-first. Speech end should be slower, conservative, merge-friendly."

**What this means:**
- VAD should fire on suspicion of speech (high recall, low precision on start)
- End-of-utterance detection should resist false endings (pause, stutter, thinking)
- The current dual-threshold approach (RMS + spectral band) is directionally right but hand-tuned

**Resolution path**: Silero VAD v5 (ONNX, <2ms/chunk) replaces the spectral detector. Already stubbed in `pipeline/silero_vad.py`.

### 2. Mind daemon must leave server.py

> "Running mind pulses on llm_gate inside server.py is an interim design that blocks the reflex path. Migration to apps/mindd/ removes this contention."

**Current state**: `mind_loop` asyncio task in `server.py` shares `llm_gate` with the reflex path.

**Target state**: `apps/mindd/` process with its own MLX sidecar (0.8–4B). Voice preempts background via `asyncio.wait()`.

### 3. Local spool durability caveat

> "The spool defaults to /tmp — this does not survive OS reboot. Do not describe this as zero data loss."

**Resolution**: Documented in ARCHITECTURE.md §4.9. Default path warning added. Reboot-safe path available via spool constructor.

### 4. Retrieval must be filtered, not pure ANN

> "Pure vector search will surface semantically similar but temporally irrelevant memories. Relational prefilter → HNSW → iterative scan → fusion rerank is the correct stack."

**Current state**: Implemented as filtered ANN in `memory/retrieval.py`.

### 5. The initiative problem

> "GARY must earn the right to speak first. Shadow mode for 2 weeks minimum. Log what it would have said, measure helpfulness rate, graduate to audible only above 60%."

**Implementation path**: `initiative_logs` table, shadow mode flag, score formula: `excitement × open_loop_urgency × validated_idea_readiness`.

### 6. Training data must be request→outcome, not transcripts

> "Strip intermediate reasoning. Only request + outcome matters. This teaches the sidecar to predict profound outcomes directly."

**What NOT to train on:** chain-of-thought verbatim, emotion logs, raw transcripts.

**What to train on:** (request, final_answer, correction_signal) tuples from the distillery.

## Quality observations

> "v3 is much stronger. The remaining unreliability is now mostly a turn-taking control problem, not a model-quality problem."

The ASR, LLM, and TTS layers are all working well. The open frontier is the control plane: when to listen, when to stop listening, when the user is finished, when GARY should interject.

## Summary of residual risks

| Risk | Severity | Mitigation |
|------|----------|-----------|
| Mind daemon in server.py | Medium | Planned migration to apps/mindd/ |
| /tmp spool path | Low | Documented; path is configurable |
| Manual VAD tuning | Medium | Silero VAD v5 upgrade path exists |
| Initiative without calibration | High | Shadow mode required before enabling |
