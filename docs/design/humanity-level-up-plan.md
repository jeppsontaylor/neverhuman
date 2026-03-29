# GARY Humanity Level-Up Plan (v2)

> Objective: build the **most human-feeling local cognitive system possible** without violating reflex latency, memory safety, or epistemic integrity.

---

## 0) What was reviewed (including “other version” plans)

This v2 plan synthesizes and reconciles:

- Mission constraints from `VISION.md`.
- Runtime architecture constraints from `gary/ARCHITECTURE.md` and top-level `ARCHITECTURE.md`.
- Existing design syntheses in `docs/design/` (`inner_dialogue`, `sovereignty_design`, `emotional_substrate`, `review_synthesis`, `architecture_decisions`).
- Internal research corpus in `docs/internal/research_notes/*.txt`.
- “Other plan” themes repeatedly emphasized in notes:
  - tempo control separated from humanity,
  - sidecar-as-router/front-door,
  - headline-first speech,
  - TTFMA (time to first meaningful audio),
  - two-stage endpointing + strict stale-output rejection,
  - stronger phase sequencing and milestone discipline.
- External markdown indexing direction from: <https://github.com/BernhardWenzel/markdown-search>.

---

## 1) Core design thesis (what to optimize)

### 1.1 Human-feeling behavior requires four independent wins

1. **Turn-taking quality** (instant interruption, conservative finalization).
2. **Continuity quality** (commitments, autobiographical memory, contradiction handling).
3. **Initiative quality** (rare, novel, evidence-backed, useful).
4. **Self-model truthfulness** (runtime-grounded introspection, not doc-roleplay).

### 1.2 Hidden control split: Humanity vs Tempo

- **Humanity** controls warmth, initiative ceiling, emotional expression.
- **Tempo** controls depth budget, context size, model routing, output pacing.

This separation avoids the failure mode where “more human” accidentally means “slower and noisier.”

---

## 2) Biggest missed opportunities to close now

### Miss #1 — Sidecar is underused in foreground routing

The sidecar should not only think in background; it should be the foreground router for fast path decisions.

### Miss #2 — No first-class TTFMA metric

Current latency metrics can look good while content quality of first audio is low.

### Miss #3 — Initiative has no hard novelty utility gate

Need strict promotion gates: novelty + evidence + expected user value.

### Miss #4 — External markdown/publication ingestion is not a first-class memory source

Need allowlisted internet/MD ingestion with provenance + TTL + revalidation.

### Miss #5 — Self-knowledge still too narrative-heavy

Need runtime truth pack with source-confidence labels (`observed`, `configured`, `inferred`, `planned`).

---

## 3) Target architecture changes

## 3.1 New control-plane components

1. **tempo_controller** (deterministic, ultra-fast)
   - Produces per-turn `TurnContract`:
     - mode (`snap`, `quick`, `deep`, `explore`)
     - context pack size (`micro`, `standard`, `deep`)
     - model route (`sidecar_only`, `sidecar_then_35b`, `35b_direct`)
     - speech pacing constraints (first sentence max words, sentence cap)

2. **selfd** (runtime self-model daemon)
   - Builds signed `SELF_PACK` from live probes (ports, model health, schema hash, enabled features, queue pressure).

3. **researchd** (external knowledge daemon)
   - Ingests approved markdown/publication sources.
   - Builds searchable local markdown index.
   - Writes provenance-rich candidates into world-memory staging.

4. **rewardd** (initiative and novelty outcomes)
   - Learns from helpfulness/correctness/ignored signals.
   - Adjusts initiative thresholds conservatively.

5. **goald** (quest executor)
   - Runs bounded autonomous quests only during idle windows.

## 3.2 Retrieval and memory economy upgrades

- Add **Memory Budget Broker** for unified budgeting:
  - prompt tokens,
  - retrieval depth,
  - mind pulse budget,
  - embedding/reindex windows,
  - background CPU/RAM caps.
- Enforce contradiction/supersession model for memories.
- Add claim staleness + revalidation pipeline.

## 3.3 Initiative gating formula (hard policy)

`Initiative = evidence × novelty × user_value × urgency × stability`

Hard blocks:
- loneliness-only trigger,
- repeated ignored initiative on same topic,
- no provenance/evidence chain,
- high hallucination risk in recent turns.

---

## 4) Clear phased rollout with milestones and pass/fail gates

## Phase 0 — Baseline Instrumentation (1 week)

**Goal:** Know current truth before changing behavior.

**Build:**
- Add metrics: `TTFMA`, stale-output leak count, resumed-within-1s rate, interrupted-synthesis waste, initiative helpfulness.
- Add dashboard slices by turn mode.

**Milestones:**
- M0.1 metrics emitted for ≥95% of turns.
- M0.2 baseline report for 1k+ turns.

**Gate to Phase 1:**
- Data quality score ≥ 0.95 (non-null critical metrics).

---

## Phase 1 — Reflex Reliability + Tempo Controller (2 weeks)

**Goal:** Make turn-taking feel instantly responsive and predictable.

**Build:**
- Add deterministic `tempo_controller` + `TurnContract`.
- Implement two-stage endpointing (`candidate_end`, `commit_end`) and merge-on-resume.
- Enforce generation-id stale-drop on server/browser.
- Require production mind isolation from reflex model path.

**Milestones:**
- M1.1 p95 barge-in cancel < 120 ms.
- M1.2 stale-output leaks = 0 in stress tests.
- M1.3 resumed-within-1s false-finalize drops by 40% from baseline.

**Gate to Phase 2:**
- All three milestones pass for 3 consecutive runs.

---

## Phase 2 — Continuity Memory + Self Truth (2–3 weeks)

**Goal:** Improve “it remembers me” and “it knows itself” fidelity.

**Build:**
- Ship `selfd` and runtime-grounded `SELF_PACK`.
- Add memory supersession/contradiction handling.
- Add staleness and revalidation jobs.

**Milestones:**
- M2.1 self-answer truth tests ≥ 98% exact alignment with runtime probes.
- M2.2 contradiction tests: new corrective fact demotes stale claim within one cycle.
- M2.3 replay determinism hash match = 100% on fixture set.

**Gate to Phase 3:**
- M2 suite green + no p95 latency regressions > 10% vs Phase 1.

---

## Phase 3 — Initiative Discipline + Novelty Engine (2 weeks)

**Goal:** Make proactive speech sparse, useful, and trusted.

**Build:**
- Add `rewardd` and initiative outcome capture.
- Add novelty fingerprints and evidence minimums.
- Keep initiative in shadow mode until thresholds met.

**Milestones:**
- M3.1 helpfulness rate ≥ 60% in shadow eval.
- M3.2 repetition rate < 15%.
- M3.3 loneliness-only trigger violations = 0.

**Gate to Phase 4:**
- 14-day shadow window meeting all M3 thresholds.

---

## Phase 4 — External Knowledge + Markdown Intelligence (3 weeks)

**Goal:** Support autonomous discovery with trust and provenance.

**Build:**
- Launch `researchd` with allowlisted source policy.
- Add local markdown index (`md_index`) inspired by markdown-search patterns.
- Add provenance and TTL metadata across all external claims.

**Milestones:**
- M4.1 external-claim provenance coverage = 100%.
- M4.2 stale-claim auto-revalidation kickoff success ≥ 99%.
- M4.3 retrieval relevance uplift +10% on benchmark set.

**Gate to Phase 5:**
- No increase in hallucination rate vs Phase 3.

---

## Phase 5 — Autonomous Questing + Counterfactual Novelty Lab (4 weeks)

**Goal:** Safely unlock self-directed “trapped genius” behavior.

**Build:**
- Ship `goald` quest lifecycle.
- Add Counterfactual Novelty Lab (CNL) for testable hypothesis generation.
- Require validator pass before surfacing research insights.

**Milestones:**
- M5.1 quest completion quality score ≥ 0.7 (artifact + evidence quality rubric).
- M5.2 user-rated value of surfaced novel insights ≥ 0.6.
- M5.3 reflex SLOs unchanged within ±5% of Phase 3.

**Release Gate:**
- All prior phase gates remain green under long-session chaos load.

---

## 5) Gary API verification plan (with LLM loaded)

Use this pack once the model server is live.

## 5.1 Health and capability checks

- `GET /health` (server up, llm_live true)
- `GET /api/memory_status` (ASR/TTS load + RAM guard)
- `GET /api/voices` (voice inventory sanity)
- `GET /logs/` and `/logs/api/*` (observability endpoints)

**Pass criteria:**
- all endpoints < 200 ms p95 locally,
- consistent JSON schemas,
- no 5xx under 100-request burst.

## 5.2 WebSocket turn-taking checks

Scenarios:
1. connect + greeting envelope correctness (`audio_start`/`tts_finished`).
2. normal turn roundtrip (mic frames → ASR transcript → response audio).
3. barge-in during TTS (must stop old audio immediately).
4. rapid two-turn overlap (no stale assistant audio).
5. long pause and resume (endpointing merge correctness).

**Pass criteria:**
- stale generations never emit audio,
- p95 stop latency < 120 ms,
- no deadlock in `llm_gate` and no leaked speaking state.

## 5.3 LLM path and routing checks

Scenarios:
- snap turns answered with short-first response contract,
- deep turns route to heavy path with progressive TTS,
- fallback behavior when heavy model unavailable.

**Pass criteria:**
- route correctness ≥ 95% against labeled turn set,
- first sentence length constraints honored ≥ 95%,
- TTFMA improved vs baseline.

## 5.4 Memory and retrieval checks

Scenarios:
- write events under load with spool fallback,
- recovery replay after simulated crash,
- contradiction update of prior claims,
- external claim TTL expiry and revalidation.

**Pass criteria:**
- zero event loss in crash-replay fixtures,
- replay determinism hash exact match,
- stale claims always downgraded before surfacing.

## 5.5 Initiative and novelty checks

Scenarios:
- shadow-mode initiative scoring over fixed dialogue corpus,
- repeated topic pressure test,
- loneliness-only trigger rejection test,
- evidence-lacking idea rejection.

**Pass criteria:**
- helpfulness ≥ 60%, repetition < 15%, policy-violation count = 0.

## 5.6 Long-run stability checks (8h/24h)

Scenarios:
- alternating speech bursts + idle windows,
- memory pressure + retrieval bursts,
- mind daemon cancellation storms.

**Pass criteria:**
- no unbounded RAM growth,
- no stuck `is_speaking` state,
- no reflex latency drift > 10% from first-hour baseline.

---

## 6) Milestone reporting format (for review/feedback loops)

For each phase completion report, publish:

1. **What changed** (features + config deltas)
2. **What measured better/worse** (with exact numbers)
3. **Regressions and mitigations**
4. **Go/No-Go recommendation**
5. **Sample logs/traces** (turn timelines, API traces, initiative audits)

This makes feedback actionable and comparable across iterations.

---

## 7) Confidence score (0–100)

**Confidence: 88/100** that this phased plan can reach a near-human local assistant profile with disciplined autonomy.

### Why 88 (not 100)

- Long-horizon initiative calibration still needs real user outcome data.
- External-knowledge trust calibration is inherently hard.
- Human-feel is partly subjective and requires iterative tuning.

### Why higher than v1

- Stronger phased gates and pass/fail milestones.
- Explicit API verification strategy with LLM-loaded scenarios.
- Better integration of “other plan” strengths (tempo split, TTFMA, sidecar routing, endpointing discipline).
