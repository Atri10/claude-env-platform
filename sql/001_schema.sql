-- =============================================================================
-- claude-env :: master SQLite schema
-- File: sql/001_schema.sql
-- Purpose: single source of truth for audit, observability, memory, approvals.
-- Engine:  SQLite (WAL mode). Designed for clean migration to PostgreSQL.
-- Apply:   sqlite3 ~/.claude-env/state/claude-env.db < sql/001_schema.sql
-- =============================================================================

PRAGMA journal_mode = WAL;          -- concurrent readers + single writer
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;        -- durable enough on a single dev box
PRAGMA busy_timeout = 5000;

-- ----------------------------------------------------------------------------
-- schema_version : migration bookkeeping (mirrors Alembic-style versioning)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_version (
    version      INTEGER PRIMARY KEY,
    applied_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    description  TEXT NOT NULL
);
INSERT OR IGNORE INTO schema_version (version, description)
VALUES (1, 'initial schema: audit, observability, memory, approvals');

-- =============================================================================
-- SECTION 1 :: AUDIT  (append-only, tamper-evident hash chain)
-- =============================================================================

-- The single canonical append-only ledger. Every other audit-like table is a
-- typed projection that references back to audit_events.event_id.
-- prev_hash + event_hash form a Merkle-style chain: any edit/deletion of a
-- historical row breaks verification (see audit/audit_logger.py::verify_chain).
CREATE TABLE IF NOT EXISTS audit_events (
    event_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT    NOT NULL,           -- ISO-8601 UTC, millisecond precision
    event_type   TEXT    NOT NULL,           -- agent_action|tool_call|retrieval|memory_read|...
    actor        TEXT    NOT NULL,           -- agent id or 'human' or 'system'
    session_id   TEXT    NOT NULL,
    repo         TEXT,                        -- repo slug, nullable for global ops
    tier         INTEGER,                     -- privacy tier 0-3 at time of event
    payload_json TEXT    NOT NULL,           -- canonical JSON of the event body
    prev_hash    TEXT    NOT NULL,           -- event_hash of previous row ('GENESIS' for first)
    event_hash   TEXT    NOT NULL UNIQUE     -- sha256(prev_hash || canonical_payload)
);
CREATE INDEX IF NOT EXISTS ix_audit_ts        ON audit_events(ts);
CREATE INDEX IF NOT EXISTS ix_audit_type      ON audit_events(event_type);
CREATE INDEX IF NOT EXISTS ix_audit_session   ON audit_events(session_id);
CREATE INDEX IF NOT EXISTS ix_audit_repo      ON audit_events(repo);
CREATE INDEX IF NOT EXISTS ix_audit_actor     ON audit_events(actor);

-- Guard: forbid UPDATE and DELETE on the ledger at the DB layer.
-- (Defence in depth; the logger never issues them either.)
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
    action       TEXT NOT NULL,              -- 'plan'|'edit'|'review'|'handoff'|...
    target       TEXT,                       -- file path / symbol / task id
    summary      TEXT,
    success      INTEGER NOT NULL DEFAULT 1, -- 0/1
    ts           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_agent_actions_agent ON agent_actions(agent);
CREATE INDEX IF NOT EXISTS ix_agent_actions_ts    ON agent_actions(ts);

CREATE TABLE IF NOT EXISTS tool_calls (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     INTEGER NOT NULL REFERENCES audit_events(event_id),
    tool         TEXT NOT NULL,             -- mcp server / tool name
    args_json    TEXT,
    result_kind  TEXT,                      -- 'ok'|'error'|'blocked'
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
    memory_type  TEXT NOT NULL,             -- episodic|semantic|procedural|agent
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
    operation    TEXT NOT NULL,             -- 'insert'|'supersede'|'consolidate'|'prune'
    ts           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_memwrites_ns ON memory_writes(namespace);

CREATE TABLE IF NOT EXISTS security_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     INTEGER NOT NULL REFERENCES audit_events(event_id),
    category     TEXT NOT NULL,             -- 'prompt_injection'|'secret'|'rag_poison'|'mem_corruption'
    severity     TEXT NOT NULL,             -- 'low'|'medium'|'high'|'critical'
    detail       TEXT NOT NULL,
    source       TEXT,                      -- file / chunk / query that triggered it
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
    rule         TEXT NOT NULL,             -- which rule matched
    decision     TEXT NOT NULL,            -- 'block'|'redact'
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
    action       TEXT NOT NULL,             -- description of the gated action
    decision     TEXT,                      -- 'approved'|'denied'|'pending'
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
    component    TEXT NOT NULL,             -- 'rag.retrieve'|'rag.rerank'|'memory.query'|'mcp.<srv>'
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
    rerank_delta REAL,                       -- mean score change after rerank
    user_feedback INTEGER                    -- optional -1/0/+1
);

-- =============================================================================
-- SECTION 3 :: MEMORY GRAPH  (local graph stored as nodes + edges in SQLite)
-- =============================================================================
-- A property graph encoded relationally. Cypher-like traversal is implemented
-- in memory/memory_retriever.py via recursive CTEs over memory_edges.
CREATE TABLE IF NOT EXISTS memory_nodes (
    node_id      TEXT PRIMARY KEY,           -- uuid4
    namespace    TEXT NOT NULL,              -- 'proj-<slug>' | 'global' | 'agent:<id>'
    memory_type  TEXT NOT NULL,              -- episodic|semantic|procedural|agent
    node_kind    TEXT NOT NULL,              -- entity|session|decision|workflow|concept|preference
    name         TEXT NOT NULL,
    body_json    TEXT NOT NULL,              -- arbitrary structured content
    repo         TEXT,
    confidence   REAL NOT NULL DEFAULT 1.0,  -- 0..1, decays over time
    half_life_days REAL NOT NULL DEFAULT 90, -- decay rate per node_kind
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    last_access  TEXT NOT NULL,
    access_count INTEGER NOT NULL DEFAULT 0,
    superseded_by TEXT REFERENCES memory_nodes(node_id),
    embedding    BLOB                        -- optional float32 vector for semantic recall
);
CREATE INDEX IF NOT EXISTS ix_memnodes_ns    ON memory_nodes(namespace);
CREATE INDEX IF NOT EXISTS ix_memnodes_type  ON memory_nodes(memory_type);
CREATE INDEX IF NOT EXISTS ix_memnodes_kind  ON memory_nodes(node_kind);
CREATE INDEX IF NOT EXISTS ix_memnodes_conf  ON memory_nodes(confidence);

CREATE TABLE IF NOT EXISTS memory_edges (
    edge_id      TEXT PRIMARY KEY,           -- uuid4
    namespace    TEXT NOT NULL,
    src          TEXT NOT NULL REFERENCES memory_nodes(node_id),
    dst          TEXT NOT NULL REFERENCES memory_nodes(node_id),
    rel          TEXT NOT NULL,              -- RELATES_TO|DEPENDS_ON|DECISION_ABOUT|DISCOVERED_IN|SUPERSEDES
    weight       REAL NOT NULL DEFAULT 1.0,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_memedges_src ON memory_edges(src);
CREATE INDEX IF NOT EXISTS ix_memedges_dst ON memory_edges(dst);
CREATE INDEX IF NOT EXISTS ix_memedges_rel ON memory_edges(rel);

-- =============================================================================
-- SECTION 4 :: RAG INDEX BOOKKEEPING (the vectors live in LanceDB; state here)
-- =============================================================================
CREATE TABLE IF NOT EXISTS rag_index_state (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    repo         TEXT NOT NULL,
    branch       TEXT NOT NULL,
    table_name   TEXT NOT NULL,             -- LanceDB table = '<repo>__<branch>'
    last_commit  TEXT,                      -- last indexed git sha
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
    content_hash TEXT NOT NULL,             -- sha256 of file at index time
    chunk_count  INTEGER NOT NULL DEFAULT 0,
    indexed_at   TEXT NOT NULL,
    UNIQUE(repo, branch, file_path)
);
CREATE INDEX IF NOT EXISTS ix_ragfiles_repo ON rag_file_state(repo, branch);
