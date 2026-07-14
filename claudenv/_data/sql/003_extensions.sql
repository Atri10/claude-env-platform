-- =============================================================================
-- claude-env :: schema extensions — retrieval feedback + session ingestion
-- File: sql/003_extensions.sql
-- Purpose: tables backing the retrieval feedback loop and the Claude Code
--          session-transcript ingestor. Lossy/operational data — NOT part of
--          the audit chain (audit rows are still written via AuditLogger).
-- Apply:   via lib/db.py::apply_schema (bootstrap.py runs this automatically).
-- =============================================================================

INSERT OR IGNORE INTO schema_version (version, description)
VALUES (3, 'extensions: rag chunk feedback, session ingest state');

-- ----------------------------------------------------------------------------
-- rag_chunk_feedback : per-chunk retrieval usage signals.
--   signal='retrieved' — chunk was returned to an agent for a query
--   signal='used'      — the file behind a retrieved chunk was subsequently
--                        edited/cited in a session (heuristic correlation by
--                        the session ingestor)
-- The retrieve pipeline aggregates 'used' counts into a small ranking boost.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rag_chunk_feedback (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    repo         TEXT NOT NULL,
    branch       TEXT NOT NULL,
    chunk_id     TEXT NOT NULL,
    file_path    TEXT,
    query_hash   TEXT,                       -- sha256[:16] of the query text
    signal       TEXT NOT NULL,              -- 'retrieved' | 'used'
    session_id   TEXT
);
CREATE INDEX IF NOT EXISTS ix_chunkfb_repo   ON rag_chunk_feedback(repo, branch);
CREATE INDEX IF NOT EXISTS ix_chunkfb_chunk  ON rag_chunk_feedback(chunk_id);
CREATE INDEX IF NOT EXISTS ix_chunkfb_signal ON rag_chunk_feedback(signal);
CREATE INDEX IF NOT EXISTS ix_chunkfb_file   ON rag_chunk_feedback(file_path);

-- ----------------------------------------------------------------------------
-- session_ingest_state : which Claude Code transcripts have been ingested into
-- the memory graph (dedupe bookkeeping for memory/session_ingestor.py).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS session_ingest_state (
    session_id      TEXT PRIMARY KEY,        -- transcript file stem (Claude Code session uuid)
    transcript_path TEXT NOT NULL,
    repo            TEXT,
    node_id         TEXT,                    -- memory node created (NULL if skipped)
    events          INTEGER NOT NULL DEFAULT 0,
    ingested_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ingest_repo ON session_ingest_state(repo);
