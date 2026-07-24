-- =============================================================================
-- claude-env :: master SQLite schema (single file — idempotent via IF NOT EXISTS)
-- Purpose: single source of truth for audit, observability, memory, approvals,
--          RAG bookkeeping, retention, and operational state.
-- Engine:  SQLite (WAL mode). Designed for clean migration to PostgreSQL.
-- Apply:   applied by claude-env init via SQLiteDatabase.apply_schema()
-- =============================================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;

-- =============================================================================
-- SECTION 1 :: AUDIT  (append-only, tamper-evident hash chain)
-- =============================================================================

-- The single canonical append-only ledger. Every other audit-like table is a
-- typed projection referencing audit_events.event_id.
-- prev_hash + event_hash form a Merkle-style chain: any edit/deletion of a
-- historical row breaks verification.
CREATE TABLE IF NOT EXISTS audit_events (
    event_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT    NOT NULL,      -- ISO-8601 UTC, millisecond precision
    event_type      TEXT    NOT NULL,      -- agent_action|tool_call|retrieval|memory_read|...
    actor           TEXT    NOT NULL,
    session_id      TEXT    NOT NULL,
    repo            TEXT,                   -- repo slug, nullable for global ops
    tier            INTEGER,                -- privacy tier 0-3 at time of event
    payload_json    TEXT    NOT NULL,      -- canonical JSON of the event body
    prev_hash       TEXT    NOT NULL,      -- event_hash of previous row (GENESIS for first)
    event_hash      TEXT    NOT NULL UNIQUE,-- sha256(prev_hash || canonical_payload)

    -- Trace metadata (for operator forensics without parsing payload_json).
    schema_version  INTEGER,               -- envelope format version when written
    host            TEXT,                   -- machine that wrote this event
    pid             INTEGER,               -- process ID
    request_id      TEXT,                   -- per-action correlation id

    -- Enriched queryable columns (denormalised from payload_json for dashboards).
    git_commit      TEXT,                   -- git SHA at time of event (from env or payload)
    cwd             TEXT,                   -- working directory
    model           TEXT,                   -- AI model identifier (when available)
    tool_input_snapshot TEXT               -- first 2 KB of Write/Edit/Bash content
);
CREATE INDEX IF NOT EXISTS ix_audit_ts            ON audit_events(ts);
CREATE INDEX IF NOT EXISTS ix_audit_type          ON audit_events(event_type);
CREATE INDEX IF NOT EXISTS ix_audit_session       ON audit_events(session_id);
CREATE INDEX IF NOT EXISTS ix_audit_repo          ON audit_events(repo);
CREATE INDEX IF NOT EXISTS ix_audit_actor         ON audit_events(actor);
CREATE INDEX IF NOT EXISTS ix_audit_request_id    ON audit_events(request_id);
CREATE INDEX IF NOT EXISTS ix_audit_commit        ON audit_events(git_commit);
CREATE INDEX IF NOT EXISTS ix_audit_host          ON audit_events(host);

-- Guards: forbid UPDATE and DELETE on the ledger at the DB layer.
CREATE TRIGGER IF NOT EXISTS audit_events_no_update
BEFORE UPDATE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END;

CREATE TRIGGER IF NOT EXISTS audit_events_no_delete
BEFORE DELETE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END;

-- ----------------------------------------------------------------------------
-- Typed projections. Each references the canonical event for full provenance.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_actions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     INTEGER NOT NULL REFERENCES audit_events(event_id),
    agent        TEXT NOT NULL,
    action       TEXT NOT NULL,
    target       TEXT,
    summary      TEXT,
    success      INTEGER NOT NULL DEFAULT 1,
    ts           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_agent_actions_agent ON agent_actions(agent);
CREATE INDEX IF NOT EXISTS ix_agent_actions_ts    ON agent_actions(ts);

CREATE TABLE IF NOT EXISTS tool_calls (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     INTEGER NOT NULL REFERENCES audit_events(event_id),
    tool         TEXT NOT NULL,
    args_json    TEXT,
    result_kind  TEXT,
    duration_ms  INTEGER,
    ts           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tool_calls_tool ON tool_calls(tool);
CREATE INDEX IF NOT EXISTS ix_tool_calls_ts   ON tool_calls(ts);

CREATE TABLE IF NOT EXISTS retrieval_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     INTEGER NOT NULL REFERENCES audit_events(event_id),
    repo         TEXT NOT NULL,
    branch       TEXT,
    query        TEXT NOT NULL,
    top_k        INTEGER NOT NULL,
    returned     INTEGER NOT NULL,
    max_score    REAL,
    min_score    REAL,
    reranked     INTEGER NOT NULL DEFAULT 0,
    duration_ms  INTEGER,
    ts           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_retrieval_repo ON retrieval_events(repo);
CREATE INDEX IF NOT EXISTS ix_retrieval_ts   ON retrieval_events(ts);

CREATE TABLE IF NOT EXISTS memory_reads (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     INTEGER NOT NULL REFERENCES audit_events(event_id),
    namespace    TEXT NOT NULL,
    memory_type  TEXT NOT NULL,
    query        TEXT,
    hit_count    INTEGER NOT NULL DEFAULT 0,
    ts           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_memreads_ns ON memory_reads(namespace);

CREATE TABLE IF NOT EXISTS memory_writes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     INTEGER NOT NULL REFERENCES audit_events(event_id),
    namespace    TEXT NOT NULL,
    memory_type  TEXT NOT NULL,
    node_id      TEXT,
    operation    TEXT NOT NULL,
    ts           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_memwrites_ns ON memory_writes(namespace);

CREATE TABLE IF NOT EXISTS security_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     INTEGER NOT NULL REFERENCES audit_events(event_id),
    category     TEXT NOT NULL,
    severity     TEXT NOT NULL,
    detail       TEXT NOT NULL,
    source       TEXT,
    ts           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_secevents_sev ON security_events(severity);
CREATE INDEX IF NOT EXISTS ix_secevents_cat ON security_events(category);

CREATE TABLE IF NOT EXISTS policy_violations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     INTEGER NOT NULL REFERENCES audit_events(event_id),
    repo         TEXT,
    tier         INTEGER,
    path         TEXT NOT NULL,
    rule         TEXT NOT NULL,
    decision     TEXT NOT NULL,
    actor        TEXT NOT NULL,
    ts           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_polviol_repo ON policy_violations(repo);
CREATE INDEX IF NOT EXISTS ix_polviol_ts   ON policy_violations(ts);

CREATE TABLE IF NOT EXISTS human_approvals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     INTEGER NOT NULL REFERENCES audit_events(event_id),
    request_id   TEXT NOT NULL UNIQUE,
    agent        TEXT NOT NULL,
    repo         TEXT,
    tier         INTEGER,
    action       TEXT NOT NULL,
    decision     TEXT,
    decided_by   TEXT,
    requested_at TEXT NOT NULL,
    decided_at   TEXT
);
CREATE INDEX IF NOT EXISTS ix_approvals_decision ON human_approvals(decision);

-- =============================================================================
-- SECTION 2 :: OBSERVABILITY  (metrics; lossy, rotatable, not chained)
-- =============================================================================
CREATE TABLE IF NOT EXISTS metrics_sessions (
    session_id   TEXT PRIMARY KEY,
    repo         TEXT,
    started_at   TEXT NOT NULL,
    ended_at     TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    est_cost_usd REAL NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS metrics_latency (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    component    TEXT NOT NULL,
    operation    TEXT NOT NULL,
    duration_ms  INTEGER NOT NULL,
    repo         TEXT
);
CREATE INDEX IF NOT EXISTS ix_latency_comp ON metrics_latency(component);
CREATE INDEX IF NOT EXISTS ix_latency_ts   ON metrics_latency(ts);

CREATE TABLE IF NOT EXISTS metrics_retrieval_quality (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    repo         TEXT NOT NULL,
    query        TEXT NOT NULL,
    top1_score   REAL,
    mean_top_k   REAL,
    rerank_delta REAL,
    user_feedback INTEGER
);

-- =============================================================================
-- SECTION 3 :: MEMORY GRAPH
-- =============================================================================
CREATE TABLE IF NOT EXISTS memory_nodes (
    node_id      TEXT PRIMARY KEY,
    namespace    TEXT NOT NULL,
    memory_type  TEXT NOT NULL,
    node_kind    TEXT NOT NULL,
    name         TEXT NOT NULL,
    body_json    TEXT NOT NULL,
    repo         TEXT,
    confidence   REAL NOT NULL DEFAULT 1.0,
    half_life_days REAL NOT NULL DEFAULT 90,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    last_access  TEXT NOT NULL,
    access_count INTEGER NOT NULL DEFAULT 0,
    superseded_by TEXT REFERENCES memory_nodes(node_id),
    embedding    BLOB
);
CREATE INDEX IF NOT EXISTS ix_memnodes_ns    ON memory_nodes(namespace);
CREATE INDEX IF NOT EXISTS ix_memnodes_type  ON memory_nodes(memory_type);
CREATE INDEX IF NOT EXISTS ix_memnodes_kind  ON memory_nodes(node_kind);
CREATE INDEX IF NOT EXISTS ix_memnodes_conf  ON memory_nodes(confidence);

CREATE TABLE IF NOT EXISTS memory_edges (
    edge_id      TEXT PRIMARY KEY,
    namespace    TEXT NOT NULL,
    src          TEXT NOT NULL REFERENCES memory_nodes(node_id),
    dst          TEXT NOT NULL REFERENCES memory_nodes(node_id),
    rel          TEXT NOT NULL,
    weight       REAL NOT NULL DEFAULT 1.0,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_memedges_src ON memory_edges(src);
CREATE INDEX IF NOT EXISTS ix_memedges_dst ON memory_edges(dst);
CREATE INDEX IF NOT EXISTS ix_memedges_rel ON memory_edges(rel);

-- =============================================================================
-- SECTION 4 :: RAG INDEX BOOKKEEPING
-- =============================================================================
CREATE TABLE IF NOT EXISTS rag_index_state (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    repo         TEXT NOT NULL,
    branch       TEXT NOT NULL,
    table_name   TEXT NOT NULL,
    last_commit  TEXT,
    chunk_count  INTEGER NOT NULL DEFAULT 0,
    embed_model  TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    UNIQUE(repo, branch)
);

CREATE TABLE IF NOT EXISTS rag_file_state (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    repo         TEXT NOT NULL,
    branch       TEXT NOT NULL,
    file_path    TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    chunk_count  INTEGER NOT NULL DEFAULT 0,
    indexed_at   TEXT NOT NULL,
    UNIQUE(repo, branch, file_path)
);
CREATE INDEX IF NOT EXISTS ix_ragfiles_repo ON rag_file_state(repo, branch);

CREATE TABLE IF NOT EXISTS rag_chunk_feedback (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    repo         TEXT NOT NULL,
    branch       TEXT NOT NULL,
    chunk_id     TEXT NOT NULL,
    file_path    TEXT,
    query_hash   TEXT,
    signal       TEXT NOT NULL,
    session_id   TEXT
);
CREATE INDEX IF NOT EXISTS ix_chunkfb_repo   ON rag_chunk_feedback(repo, branch);
CREATE INDEX IF NOT EXISTS ix_chunkfb_chunk  ON rag_chunk_feedback(chunk_id);
CREATE INDEX IF NOT EXISTS ix_chunkfb_signal ON rag_chunk_feedback(signal);
CREATE INDEX IF NOT EXISTS ix_chunkfb_file   ON rag_chunk_feedback(file_path);

CREATE TABLE IF NOT EXISTS session_ingest_state (
    session_id      TEXT PRIMARY KEY,
    transcript_path TEXT NOT NULL,
    repo            TEXT,
    node_id         TEXT,
    events          INTEGER NOT NULL DEFAULT 0,
    ingested_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ingest_repo ON session_ingest_state(repo);

-- =============================================================================
-- SECTION 5 :: OPERATIONAL STATE  (not in the audit chain)
-- =============================================================================

-- Track which repos are onboarded (survives file deletion, queryable).
CREATE TABLE IF NOT EXISTS onboarded_repos (
    repo_slug    TEXT PRIMARY KEY,
    repo_root    TEXT NOT NULL,
    tier         INTEGER NOT NULL DEFAULT 1,
    description  TEXT DEFAULT '',
    default_branch TEXT DEFAULT 'main',
    onboarded_at TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

-- Audit trail for model downloads (supply-chain visibility).
CREATE TABLE IF NOT EXISTS model_downloads (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    model_name   TEXT NOT NULL,
    model_type   TEXT NOT NULL,             -- embedding|reranker
    backend      TEXT NOT NULL,
    source_url   TEXT,
    file_path    TEXT NOT NULL,
    file_hash    TEXT,
    size_bytes   INTEGER,
    downloaded_at TEXT NOT NULL
);

-- Track every (re)index run for visibility and forensics.
CREATE TABLE IF NOT EXISTS reindex_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    repo         TEXT NOT NULL,
    branch       TEXT NOT NULL,
    "trigger"     TEXT NOT NULL,             -- manual|git-hook|nightly|onboard
    "commit"      TEXT,
    files_total  INTEGER NOT NULL DEFAULT 0,
    files_changed INTEGER NOT NULL DEFAULT 0,
    chunks_total INTEGER NOT NULL DEFAULT 0,
    duration_ms  INTEGER,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    UNIQUE(repo, branch, started_at)
);

-- =============================================================================
-- SECTION 6 :: RETENTION + CONVENIENCE VIEWS
-- =============================================================================
CREATE TABLE IF NOT EXISTS retention_policy (
    table_name   TEXT PRIMARY KEY,
    strategy     TEXT NOT NULL,
    keep_days    INTEGER,
    archive_to   TEXT,
    notes        TEXT
);

INSERT OR REPLACE INTO retention_policy VALUES
 ('audit_events',             'append_only',        NULL, '~/.claude-env/archive/audit',   'tamper-evident, archive only'),
 ('agent_actions',            'archive_then_prune', 365,  '~/.claude-env/archive/audit',   'projection of audit_events'),
 ('tool_calls',              'archive_then_prune', 365,  '~/.claude-env/archive/audit',   'projection of audit_events'),
 ('retrieval_events',         'archive_then_prune', 180,  '~/.claude-env/archive/audit',   'projection of audit_events'),
 ('memory_reads',            'rolling',            90,   NULL,                            'high-volume, low-value long term'),
 ('memory_writes',           'archive_then_prune', 365,  '~/.claude-env/archive/audit',   'keep for provenance'),
 ('security_events',          'append_only',        NULL, '~/.claude-env/archive/security','never auto-prune'),
 ('policy_violations',        'append_only',        NULL, '~/.claude-env/archive/security','never auto-prune'),
 ('human_approvals',          'append_only',        NULL, '~/.claude-env/archive/audit',   'compliance record'),
 ('metrics_latency',          'rolling',            30,   NULL,                            'observability only'),
 ('metrics_retrieval_quality','rolling',            90,   NULL,                            'observability only'),
 ('metrics_sessions',         'archive_then_prune', 365,  '~/.claude-env/archive/metrics', 'cost history');

CREATE VIEW IF NOT EXISTS v_recent_violations AS
  SELECT pv.ts, pv.repo, pv.tier, pv.actor, pv.rule, pv.path, pv.decision
  FROM policy_violations pv ORDER BY pv.ts DESC LIMIT 200;

CREATE VIEW IF NOT EXISTS v_session_cost AS
  SELECT session_id, repo, started_at, ended_at, input_tokens, output_tokens, est_cost_usd
  FROM metrics_sessions ORDER BY started_at DESC;

CREATE VIEW IF NOT EXISTS v_rag_health AS
  SELECT repo, branch, table_name, chunk_count, last_commit, embed_model, updated_at
  FROM rag_index_state ORDER BY updated_at DESC;

CREATE VIEW IF NOT EXISTS v_open_approvals AS
  SELECT request_id, agent, repo, tier, action, requested_at
  FROM human_approvals WHERE decision IS NULL OR decision = 'pending'
  ORDER BY requested_at ASC;
