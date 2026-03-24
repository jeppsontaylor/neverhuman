-- memory/quest_schema.sql — Quest Board persistence layer
-- Quests give GARY persistent interests and continuity.

CREATE TABLE IF NOT EXISTS quests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    domain TEXT NOT NULL,                    -- science|system|user|self
    kind TEXT NOT NULL,                      -- research|repair|experiment|user_project|memory
    source TEXT NOT NULL,                    -- user|mind|mechanic|researcher|dreamer
    status TEXT NOT NULL DEFAULT 'open',     -- open|active|blocked|validated|shelved|closed|abandoned
    priority REAL DEFAULT 0.5,
    novelty REAL DEFAULT 0.0,
    evidence_refs JSONB DEFAULT '[]',
    next_action TEXT,
    owner_lane TEXT,                         -- researcher|mechanic|archivist|dreamer|scientist
    surfaced_count INT DEFAULT 0,
    reward_score REAL DEFAULT 0.0,
    closure_reason TEXT,
    surfaceability TEXT DEFAULT 'none',      -- none|summary|full
    mission_axis TEXT,                       -- science|system|user|self
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    resolved_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS quest_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    quest_id UUID REFERENCES quests(id),
    event_type TEXT NOT NULL,                -- progress|evidence|blocked|validated|abandoned
    detail TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Self-changes ledger (DB-backed live settings)
CREATE TABLE IF NOT EXISTS self_changes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    change_type TEXT NOT NULL,               -- live_setting|mission|session_overlay|code_patch
    key TEXT NOT NULL,
    old_value JSONB,
    new_value JSONB,
    source TEXT NOT NULL,                    -- user|self|system
    approved BOOLEAN DEFAULT TRUE,
    reversible BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_quests_status ON quests(status);
CREATE INDEX IF NOT EXISTS idx_quests_domain ON quests(domain);
CREATE INDEX IF NOT EXISTS idx_quest_events_quest ON quest_events(quest_id);
CREATE INDEX IF NOT EXISTS idx_self_changes_key ON self_changes(key);
