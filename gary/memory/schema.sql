-- ============================================================================
-- GARY v2 — Memory Spine Schema (v6.0)
-- Postgres 16 + pgvector
--
-- 20 tables. Questions for curiosity. Experiments for hypothesis testing.
-- Claims for epistemic truth. Initiative logs for shadow mode.
-- Training buffer for the Cognitive Flywheel.
-- Retrieval audit for context compiler. Prompt versioning for reproducibility.
-- ============================================================================

-- Enable pgvector for embedding storage
CREATE EXTENSION IF NOT EXISTS vector;

-- Enable pg_trgm for fuzzy text search
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ── 1. events ────────────────────────────────────────────────────────────────
-- Immutable, append-only. Single source of truth.
-- Partitioned by month for fast range queries + vacuum.
-- Causality fields track the chain of what caused what.
CREATE TABLE events (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    session_id  TEXT NOT NULL DEFAULT '',
    session_seq INT NOT NULL DEFAULT 0,              -- monotonic counter within session
    turn_id     TEXT NOT NULL DEFAULT '',             -- groups request+response into a turn
    actor       TEXT NOT NULL DEFAULT 'system',       -- user | assistant | system | dreamer | validator
    kind        TEXT NOT NULL DEFAULT 'session',      -- utterance | response | thought | affect | idea | tool | error | session
    payload     JSONB NOT NULL DEFAULT '{}',          -- flexible per-kind data
    artifact_id UUID,                                 -- FK to artifacts (nullable)
    parent_id   UUID,                                 -- FK to parent event (causality chain)
    epistemic_status TEXT NOT NULL DEFAULT 'observed', -- observed | user_asserted | inferred | imagined | validated | superseded | retracted
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
) PARTITION BY RANGE (ts);

-- Create initial partitions
CREATE TABLE events_2026_03 PARTITION OF events
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE events_2026_04 PARTITION OF events
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE events_2026_05 PARTITION OF events
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE events_default PARTITION OF events DEFAULT;

CREATE INDEX idx_events_session ON events (session_id, ts);
CREATE INDEX idx_events_session_seq ON events (session_id, session_seq);
CREATE INDEX idx_events_turn ON events (turn_id) WHERE turn_id != '';
CREATE INDEX idx_events_kind ON events (kind, ts);
CREATE INDEX idx_events_actor ON events (actor, ts);
CREATE INDEX idx_events_parent ON events (parent_id) WHERE parent_id IS NOT NULL;
CREATE INDEX idx_events_payload_text ON events USING gin ((payload->>'text') gin_trgm_ops)
    WHERE kind IN ('utterance', 'response');


-- ── 2. claims ────────────────────────────────────────────────────────────────
-- Epistemic truth layer. A claim = a durable proposition with provenance.
-- "Never forget" means never lose provenance. Claims can be superseded,
-- contradicted, retracted, or confirmed — but never silently mutated.
CREATE TABLE claims (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject         TEXT NOT NULL,                   -- "user.preference.coffee"
    predicate       TEXT NOT NULL,                   -- "prefers"
    value           TEXT NOT NULL,                   -- "dark roast, no sugar"
    domain          TEXT NOT NULL DEFAULT 'user',    -- user | relationship | self | world
    confidence      REAL NOT NULL DEFAULT 0.5,       -- 0.0-1.0
    epistemic_status TEXT NOT NULL DEFAULT 'inferred', -- observed | user_asserted | inferred | validated | superseded | retracted
    source_event_ids UUID[] NOT NULL DEFAULT '{}',   -- provenance: which events support this
    superseded_by   UUID,                            -- FK: newer claim that replaces this
    contradicted_by UUID,                            -- FK: claim that contradicts this
    last_confirmed_at TIMESTAMPTZ,                   -- when was this last verified
    stale_after     INTERVAL,                        -- optionally mark as stale after N days
    metadata        JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_claims_subject ON claims (subject);
CREATE INDEX idx_claims_domain ON claims (domain, epistemic_status);
CREATE INDEX idx_claims_active ON claims (domain, confidence DESC)
    WHERE epistemic_status NOT IN ('superseded', 'retracted');
CREATE INDEX idx_claims_stale ON claims (last_confirmed_at)
    WHERE stale_after IS NOT NULL;


-- ── 3. memories ──────────────────────────────────────────────────────────────
-- Compressed, embedded, typed. Each memory tier compresses from below.
CREATE TABLE memories (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind            TEXT NOT NULL,                   -- episodic | semantic | social | procedural | dossier
    title           TEXT NOT NULL DEFAULT '',
    content         TEXT NOT NULL,
    embedding       vector(384),                     -- all-MiniLM-L6-v2 (384d)
    salience        REAL NOT NULL DEFAULT 0.5,       -- 0.0-1.0, updated by reward system
    retention_tier  TEXT NOT NULL DEFAULT 'hot',     -- hot | warm | cold
    source_event_id UUID,                            -- provenance
    metadata        JSONB NOT NULL DEFAULT '{}',     -- extra per-kind fields
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    superseded_by   UUID,                            -- if compressed into a higher-tier memory
    expires_at      TIMESTAMPTZ                      -- TTL for hot/warm tier pruning
);

CREATE INDEX idx_memories_kind ON memories (kind);
CREATE INDEX idx_memories_tier ON memories (retention_tier, salience DESC);
CREATE INDEX idx_memories_salience ON memories (salience DESC);
CREATE INDEX idx_memories_embedding ON memories USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 50);
CREATE INDEX idx_memories_metadata ON memories USING gin (metadata);
CREATE INDEX idx_memories_expiry ON memories (expires_at)
    WHERE expires_at IS NOT NULL;


-- ── 4. thoughts ──────────────────────────────────────────────────────────────
-- Structured inner dialogue objects: microtraces (tiny) and reflections (richer)
CREATE TABLE thoughts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      TEXT NOT NULL DEFAULT '',
    lane            TEXT NOT NULL,                   -- observer | questioner | dreamer | scientist
    pulse_type      TEXT NOT NULL DEFAULT 'microtrace', -- microtrace | reflection | brainstorm | dream | consolidation
    content         TEXT NOT NULL,
    salience        REAL NOT NULL DEFAULT 0.5,
    may_surface     BOOLEAN NOT NULL DEFAULT FALSE,
    surfaced        BOOLEAN NOT NULL DEFAULT FALSE,
    emotional_color TEXT NOT NULL DEFAULT 'neutral',
    epistemic_status TEXT NOT NULL DEFAULT 'inferred',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ                      -- microtraces expire quickly unless promoted
);

CREATE INDEX idx_thoughts_session ON thoughts (session_id, created_at);
CREATE INDEX idx_thoughts_salience ON thoughts (salience DESC) WHERE may_surface;
CREATE INDEX idx_thoughts_expiry ON thoughts (expires_at)
    WHERE expires_at IS NOT NULL;


-- ── 5. affect_state ──────────────────────────────────────────────────────────
-- Sparse snapshots of the 13-dimension emotional vector.
-- EMA runs in RAM, writes only on threshold change or interval.
CREATE TABLE affect_state (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id  TEXT NOT NULL DEFAULT '',
    dimensions  JSONB NOT NULL,                 -- {valence: 0.3, loneliness: 0.7, ...}
    trigger     TEXT NOT NULL DEFAULT '',        -- what caused this snapshot
    appraisal   JSONB NOT NULL DEFAULT '{}',    -- appraisal layer output for this trigger
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_affect_session ON affect_state (session_id, created_at DESC);


-- ── 6. open_loops ────────────────────────────────────────────────────────────
-- Unified: threads + promises + reminders + commitments
CREATE TABLE open_loops (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind            TEXT NOT NULL,                   -- thread | promise | reminder | commitment
    title           TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'open',    -- open | progressing | resolved | abandoned
    priority        REAL NOT NULL DEFAULT 0.5,
    due_at          TIMESTAMPTZ,
    source_event_id UUID,
    resolved_event_id UUID,
    metadata        JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_open_loops_status ON open_loops (status, priority DESC)
    WHERE status IN ('open', 'progressing');
CREATE INDEX idx_open_loops_due ON open_loops (due_at)
    WHERE due_at IS NOT NULL AND status = 'open';


-- ── 7. ideas ─────────────────────────────────────────────────────────────────
-- Hypothesis lab with epistemic lifecycle
CREATE TABLE ideas (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hypothesis      TEXT NOT NULL,
    evidence_for    JSONB NOT NULL DEFAULT '[]',     -- [{event_id, summary}]
    evidence_against JSONB NOT NULL DEFAULT '[]',
    epistemic_status TEXT NOT NULL DEFAULT 'imagined', -- imagined | inferred | validated | superseded
    confidence      REAL NOT NULL DEFAULT 0.0,
    evidence_score  REAL NOT NULL DEFAULT 0.0,       -- net evidence strength
    source_thought_id UUID,
    validated_at    TIMESTAMPTZ,
    metadata        JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_ideas_status ON ideas (epistemic_status, confidence DESC);


-- ── 8. rewards ───────────────────────────────────────────────────────────────
-- Epistemic-first dopamine ledger. Truth gate must pass before social reward counts.
CREATE TABLE rewards (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    target_type TEXT NOT NULL,                       -- memory | idea | open_loop | claim
    target_id   UUID NOT NULL,
    signal      REAL NOT NULL,                       -- positive or negative reward
    reason      TEXT NOT NULL DEFAULT '',
    truth_gated BOOLEAN NOT NULL DEFAULT FALSE,      -- if TRUE, signal only applies if claim was validated
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_rewards_target ON rewards (target_type, target_id, created_at DESC);


-- ── 9. initiative_logs ───────────────────────────────────────────────────────
-- Shadow mode tracking: what GARY *would* have said, and whether it was welcome.
-- Critical for tuning proactivity before it gets a voice.
CREATE TABLE initiative_logs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      TEXT NOT NULL DEFAULT '',
    score           REAL NOT NULL,                   -- initiative score at the moment
    score_breakdown JSONB NOT NULL DEFAULT '{}',     -- {excitement: 0.3, urgency: 0.5, ...}
    reason_code     TEXT NOT NULL,                    -- open_loop | commitment | idea | wellbeing | exploration
    evidence_refs   UUID[] NOT NULL DEFAULT '{}',    -- what triggered this
    draft_text      TEXT NOT NULL DEFAULT '',         -- what would have been said
    presence_conf   REAL NOT NULL DEFAULT 0.0,       -- user presence confidence at the moment
    outcome         TEXT NOT NULL DEFAULT 'shadow',  -- shadow | surfaced | welcomed | ignored | regretted | rebuffed
    surfaced_at     TIMESTAMPTZ,
    outcome_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_initiative_session ON initiative_logs (session_id, created_at DESC);
CREATE INDEX idx_initiative_outcome ON initiative_logs (outcome, created_at DESC);


-- ── 10. artifacts ────────────────────────────────────────────────────────────
-- Metadata + pointers to files on disk. Never stores binary in Postgres.
CREATE TABLE artifacts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    filename        TEXT NOT NULL,
    mime_type       TEXT NOT NULL DEFAULT 'application/octet-stream',
    size_bytes      BIGINT,
    sha256          TEXT,                            -- content-addressable storage key
    storage_path    TEXT NOT NULL,                   -- relative path in CAS or hot staging
    storage_tier    TEXT NOT NULL DEFAULT 'hot',     -- hot | durable
    source_event_id UUID,
    metadata        JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_artifacts_sha ON artifacts (sha256) WHERE sha256 IS NOT NULL;


-- ── 11. voice_profiles ───────────────────────────────────────────────────────
-- Speaker embeddings, lexicons, granular biometric consent scopes
CREATE TABLE voice_profiles (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    speaker_name    TEXT NOT NULL,
    embedding       vector(256),                     -- Resemblyzer speaker embedding
    lexicon         JSONB NOT NULL DEFAULT '[]',     -- [{word, phoneme, context}]
    clone_prompt    TEXT,
    -- Granular consent (7 independent scopes)
    consent_save_audio     BOOLEAN NOT NULL DEFAULT FALSE,
    consent_save_transcript BOOLEAN NOT NULL DEFAULT FALSE,
    consent_asr_training   BOOLEAN NOT NULL DEFAULT FALSE,
    consent_voice_cloning  BOOLEAN NOT NULL DEFAULT FALSE,
    consent_long_term_memory BOOLEAN NOT NULL DEFAULT TRUE,
    consent_export         BOOLEAN NOT NULL DEFAULT FALSE,
    consent_training_sets  BOOLEAN NOT NULL DEFAULT FALSE,
    audio_hours     REAL NOT NULL DEFAULT 0.0,
    metadata        JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX idx_voice_profiles_name ON voice_profiles (speaker_name);


-- ── 12. training_buffer ──────────────────────────────────────────────────────
-- The Cognitive Flywheel: request→outcome training pairs.
-- Intermediate reasoning is stripped. Only request + final outcome survive.
-- This teaches the sidecar LLM to predict profound outcomes directly.
CREATE TABLE training_buffer (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_target    TEXT NOT NULL DEFAULT 'sidecar',  -- sidecar | asr | tts
    request         TEXT NOT NULL,                    -- what was asked / what triggered the thought
    outcome         TEXT NOT NULL,                    -- the final useful answer / validated insight
    context_used    JSONB NOT NULL DEFAULT '[]',      -- memory IDs that were relevant (retrieval audit)
    quality_signal  TEXT NOT NULL,                    -- user_accepted | user_corrected | self_validated | idea_promoted
    affect_delta    JSONB NOT NULL DEFAULT '{}',      -- how this changed emotional state
    source          TEXT NOT NULL DEFAULT 'realized', -- realized | internal | counterfactual
    -- Safety gates (all must be TRUE for training eligibility)
    speaker_conf    REAL NOT NULL DEFAULT 0.0,        -- speaker verification confidence
    consent_ok      BOOLEAN NOT NULL DEFAULT FALSE,
    no_tts_bleed    BOOLEAN NOT NULL DEFAULT TRUE,
    holdout_bucket  INT,                              -- assigned before training (0-9)
    consumed_by     UUID,                             -- FK to training_runs once used
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_training_eligible ON training_buffer (model_target, quality_signal)
    WHERE consumed_by IS NULL AND consent_ok AND speaker_conf > 0.8;
CREATE INDEX idx_training_source ON training_buffer (source, created_at DESC);


-- ── training_runs ────────────────────────────────────────────────────────────
-- Model registry: adapters, datasets, metrics, rollback (same as before)
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

CREATE INDEX idx_training_model ON training_runs (model_type, status, created_at DESC);


-- ── 13. questions (curiosity ledger) ─────────────────────────────────────────
-- Tracks what GARY is curious about. Drives the cognitive agenda.
-- Questions can be user-posed or self-generated.
CREATE TABLE questions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scope           TEXT NOT NULL DEFAULT 'user',      -- user | self | world
    kind            TEXT NOT NULL DEFAULT 'unresolved', -- unresolved | exploring | answered | abandoned
    text            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'open',       -- open | answered | abandoned
    salience        REAL NOT NULL DEFAULT 0.5,
    may_surface     BOOLEAN NOT NULL DEFAULT FALSE,     -- can this be asked aloud?
    source_event_ids UUID[] NOT NULL DEFAULT '{}',
    answer_claim_id UUID,                               -- FK to claims if answered
    embedding       vector(384),
    next_review_at  TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_questions_status ON questions (status, salience DESC)
    WHERE status = 'open';
CREATE INDEX idx_questions_scope ON questions (scope, status);
CREATE INDEX idx_questions_embedding ON questions USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 20);


-- ── 14. experiments ──────────────────────────────────────────────────────────
-- Hypothesis testing: dream → test → claim loop
CREATE TABLE experiments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idea_id         UUID NOT NULL REFERENCES ideas(id),
    question_id     UUID REFERENCES questions(id),
    test_type       TEXT NOT NULL,                      -- observation | prediction | conversation_probe | tool_check
    plan            JSONB NOT NULL DEFAULT '{}',
    result          JSONB,
    verdict         TEXT,                               -- confirmed | refuted | inconclusive
    evidence_refs   UUID[] NOT NULL DEFAULT '{}',
    confidence_delta REAL DEFAULT 0.0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_experiments_idea ON experiments (idea_id, created_at DESC);
CREATE INDEX idx_experiments_verdict ON experiments (verdict)
    WHERE verdict IS NOT NULL;


-- ── 15. retrieval_log ────────────────────────────────────────────────────────
-- Audit trail: what context was compiled into each turn
CREATE TABLE retrieval_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pack_hash       TEXT NOT NULL,                      -- sha256 of compiled context
    turn_id         TEXT NOT NULL DEFAULT '',
    retrieved_ids   UUID[] NOT NULL DEFAULT '{}',       -- IDs of claims/loops/memories used
    slot_counts     JSONB NOT NULL DEFAULT '{}',        -- {open_loops: 2, claims: 3, ...}
    used_in_answer  BOOLEAN,                            -- did the answer reference this?
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_retrieval_turn ON retrieval_log (turn_id);
CREATE INDEX idx_retrieval_hash ON retrieval_log (pack_hash);


-- ── 16. prompt_versions ──────────────────────────────────────────────────────
-- Prompt registry: track which prompt was active for each agent at each time
CREATE TABLE prompt_versions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_name      TEXT NOT NULL,                      -- reflex_core | mind_daemon | reflector | brainstormer | ...
    version         TEXT NOT NULL,
    sha256          TEXT NOT NULL,
    prompt_text     TEXT NOT NULL,
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_prompt_active ON prompt_versions (agent_name, active)
    WHERE active = TRUE;
CREATE UNIQUE INDEX idx_prompt_sha ON prompt_versions (agent_name, sha256);


-- ── 17. consent_log ──────────────────────────────────────────────────────────
-- Audit trail for consent changes (voice data governance)
CREATE TABLE consent_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    speaker_id      UUID NOT NULL REFERENCES voice_profiles(id),
    scope           TEXT NOT NULL,                      -- save_audio | save_transcript | asr_training | voice_cloning | long_term_memory | export | training_sets
    granted         BOOLEAN NOT NULL,
    source          TEXT NOT NULL DEFAULT 'explicit',   -- explicit | inferred | revoked
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_consent_speaker ON consent_log (speaker_id, scope, created_at DESC);


-- ── 18. claim_edges ──────────────────────────────────────────────────────────
-- Provenance graph: typed relationships between claims, ideas, experiments
CREATE TABLE claim_edges (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id       UUID NOT NULL,
    target_id       UUID NOT NULL,
    edge_type       TEXT NOT NULL,                      -- supports | contradicts | corrects | fulfills | supersedes | caused_by | inspired
    confidence      REAL DEFAULT 0.5,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_claim_edges_source ON claim_edges (source_id, edge_type);
CREATE INDEX idx_claim_edges_target ON claim_edges (target_id, edge_type);


-- ── 19. eval_runs ────────────────────────────────────────────────────────────
-- Evaluation harness: captures metrics from automated test runs
CREATE TABLE eval_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    harness         TEXT NOT NULL,                      -- latency | initiative | retrieval | dream_accuracy
    metrics         JSONB NOT NULL DEFAULT '{}',
    prompt_version_ids UUID[] DEFAULT '{}',
    notes           TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_eval_harness ON eval_runs (harness, created_at DESC);


-- ── open_loops extensions (commitment support) ───────────────────────────────
-- Extends open_loops with fields needed by the Commitment Engine
ALTER TABLE open_loops ADD COLUMN IF NOT EXISTS affective_charge REAL DEFAULT 0.0;
ALTER TABLE open_loops ADD COLUMN IF NOT EXISTS source_refs UUID[] DEFAULT '{}';
ALTER TABLE open_loops ADD COLUMN IF NOT EXISTS last_touched_at TIMESTAMPTZ DEFAULT now();
ALTER TABLE open_loops ADD COLUMN IF NOT EXISTS turn_id TEXT DEFAULT '';


-- ── LISTEN/NOTIFY trigger ────────────────────────────────────────────────────
-- Wake-up signal only. Payload is minimal (just IDs). Workers fetch real data
-- from tables. This follows PG best practice: NOTIFY as nudge, not transport.
CREATE OR REPLACE FUNCTION notify_new_event() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify('gary_events', json_build_object(
        'id', NEW.id,
        'kind', NEW.kind
    )::text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_notify_event
    AFTER INSERT ON events
    FOR EACH ROW EXECUTE FUNCTION notify_new_event();


-- ── Helper views ─────────────────────────────────────────────────────────────

-- Active open loops with urgency scoring
CREATE VIEW v_active_loops AS
SELECT
    id, kind, title, status, priority, due_at,
    CASE
        WHEN due_at IS NOT NULL AND due_at < now() THEN 1.0
        WHEN due_at IS NOT NULL AND due_at < now() + interval '1 hour' THEN 0.8
        WHEN due_at IS NOT NULL AND due_at < now() + interval '1 day' THEN 0.5
        ELSE priority
    END AS urgency
FROM open_loops
WHERE status IN ('open', 'progressing')
ORDER BY urgency DESC;

-- Active claims (not superseded/retracted) for retrieval prefiltering
CREATE VIEW v_active_claims AS
SELECT id, subject, predicate, value, domain, confidence, epistemic_status,
       last_confirmed_at, created_at
FROM claims
WHERE epistemic_status NOT IN ('superseded', 'retracted')
ORDER BY confidence DESC;

-- Training-eligible pairs (all safety gates pass)
CREATE VIEW v_training_eligible AS
SELECT id, model_target, request, outcome, context_used,
       quality_signal, affect_delta, source, holdout_bucket
FROM training_buffer
WHERE consumed_by IS NULL
  AND consent_ok = TRUE
  AND speaker_conf > 0.8
  AND no_tts_bleed = TRUE
ORDER BY created_at;
