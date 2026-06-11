-- =============================================================================
-- File: sql/002_retention.sql
-- Purpose: retention + archival policy. Audit ledger is NEVER deleted in place
--          (append-only). It is archived to cold parquet, then a verified
--          archival checkpoint allows the active DB to be vacuumed of OLD rows
--          ONLY via the documented archive_audit.py path, which re-anchors the
--          chain. Metrics/observability tables ARE prunable.
-- =============================================================================

CREATE TABLE IF NOT EXISTS retention_policy (
    table_name   TEXT PRIMARY KEY,
    strategy     TEXT NOT NULL,             -- 'append_only'|'rolling'|'archive_then_prune'
    keep_days    INTEGER,                   -- NULL = keep forever
    archive_to   TEXT,                      -- parquet dir, NULL = no archive
    notes        TEXT
);

INSERT OR REPLACE INTO retention_policy VALUES
 ('audit_events',             'append_only',        NULL, '~/.claude-env/archive/audit',   'tamper-evident; archive only'),
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

-- Convenience views for the dashboard / runbook queries.
CREATE VIEW IF NOT EXISTS v_recent_violations AS
  SELECT pv.ts, pv.repo, pv.tier, pv.actor, pv.rule, pv.path, pv.decision
  FROM policy_violations pv
  ORDER BY pv.ts DESC
  LIMIT 200;

CREATE VIEW IF NOT EXISTS v_session_cost AS
  SELECT session_id, repo, started_at, ended_at,
         input_tokens, output_tokens, est_cost_usd
  FROM metrics_sessions
  ORDER BY started_at DESC;

CREATE VIEW IF NOT EXISTS v_rag_health AS
  SELECT repo, branch, table_name, chunk_count, last_commit, embed_model, updated_at
  FROM rag_index_state
  ORDER BY updated_at DESC;

CREATE VIEW IF NOT EXISTS v_open_approvals AS
  SELECT request_id, agent, repo, tier, action, requested_at
  FROM human_approvals
  WHERE decision IS NULL OR decision = 'pending'
  ORDER BY requested_at ASC;
