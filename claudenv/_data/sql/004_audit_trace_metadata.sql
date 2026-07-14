-- =============================================================================
-- claude-env :: audit ledger governance/trace metadata
-- File: sql/004_audit_trace_metadata.sql
-- Purpose: extend audit_events with per-row trace metadata (schema_version,
--          host, pid, request_id) so operators can correlate/filter events
--          without parsing payload_json, and so future envelope changes can
--          be told apart from historical rows by schema_version. All four
--          fields are also folded into the hashed canonical envelope (see
--          claudenv/domain/audit.py::AuditEvent) — tampering with them
--          breaks chain verification exactly like tampering with the body.
-- Apply:   via IDatabase.apply_schema (claudenv bootstrap runs this
--          automatically); safe to re-run (ALTER TABLE guarded by
--          pragma_table_info check, since SQLite lacks IF NOT EXISTS here).
-- =============================================================================

INSERT OR IGNORE INTO schema_version (version, description)
VALUES (4, 'audit ledger: schema_version/host/pid/request_id trace columns');

-- SQLite has no "ADD COLUMN IF NOT EXISTS"; re-running this file on a later
-- bootstrap is made safe by IDatabase.apply_schema tolerating the resulting
-- "duplicate column name" error specifically (see SQLiteDatabase.apply_schema).
ALTER TABLE audit_events ADD COLUMN schema_version INTEGER;
ALTER TABLE audit_events ADD COLUMN host           TEXT;
ALTER TABLE audit_events ADD COLUMN pid            INTEGER;
ALTER TABLE audit_events ADD COLUMN request_id     TEXT;

CREATE INDEX IF NOT EXISTS ix_audit_request_id ON audit_events(request_id);
CREATE INDEX IF NOT EXISTS ix_audit_host       ON audit_events(host);
