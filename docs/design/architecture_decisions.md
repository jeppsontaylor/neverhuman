# Architecture Decisions

> Distilled from review1–6 (architectural decision records)

## ADR-001: Process isolation model

**Decision**: Three processes (Reflex, Background, Postgres), not microservices.

**Context**: Compute-constrained (single Mac). The 35B LLM dominates GPU. No capacity for twelve services.

**Rationale**: Microservices give resilience and independent scaling. On a single machine with one GPU, they give congestion and complexity. Three hard process boundaries is the correct tradeoff.

**Consequence**: Mind daemon contends for `llm_gate` with the reflex path until `apps/mindd/` migration.

---

## ADR-002: Postgres as unified spine

**Decision**: Postgres + pgvector instead of Redis + Pinecone or SQLite + Chroma.

**Rationale**:
- Embeddings live next to relational data — no dual-system retrieval joins
- HNSW + IVFFlat indexes are mature and accurate
- LISTEN/NOTIFY is already there — no separate message bus needed
- Single Docker volume — simple backup/restore
- `tcn` extension enables row-level change notifications

---

## ADR-003: Event ledger as source of truth

**Decision**: All interactions are appended to the `events` table. All other tables (`claims`, `memories`, `thoughts`) are **derived projections**.

**Rationale**: Replay debugging. If the belief system is wrong, events can be reprocessed. Claims are not raw events — they are epistemic commitments that derive from multiple events with explicit confidence tracking.

---

## ADR-004: Filler audio before TTL

**Decision**: Pre-baked filler audio plays immediately after ASR, before the LLM starts generating.

**Rationale**: TTFT for 35B is ~1-2 seconds. Without filler audio, the user hears dead air and thinks the system is broken. Filler audio masks the LLM prefill phase and signals "I heard you, thinking."

**Implementation**: random selection from `static/audio/fillers/` — varies the response and avoids habituation.

---

## ADR-005: No-framework browser SPA

**Decision**: Pure HTML/CSS/JS for `static/index.html`. No npm, no CDN, no React.

**Rationale**: The browser SPA is served locally over TLS. No internet dependency. Any npm or CDN link would break in offline mode or add a privacy issue. Every line of frontend code is inspectable.

**Tradeoff**: No component library, no bundled TypeScript. Acceptable for a single-developer, single-file SPA.

---

## ADR-006: Flash-moe SSD streaming

**Decision**: 35B model weights streamed from SSD on-demand, not fully resident in VRAM.

**Rationale**: 35B at 4-bit = ~18GB. M-series Macs have 18-36GB unified memory shared with GPU. Full residency would consume the entire system. SSD streaming via `pread()` with 4 parallel threads + double-buffering achieves ~3-4GB resident with acceptable throughput (~3-4 tokens/s).

**Consequence**: First token latency is higher than full-VRAM models (~1-2s prefill). This is why filler audio (ADR-004) is critical.
