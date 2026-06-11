# claude-env — PostgreSQL Migration Plan

SQLite is the default and is sufficient for a single developer machine. Migrate to
PostgreSQL when you need multi-machine sharing, concurrent writers beyond one, or
centralized audit retention. Because all DB access goes through `lib/db.py`, the
application code does not change; this plan covers the schema dialect, data copy, and
cutover.

## What already abstracts cleanly
- `lib/db.py` selects the backend from the DSN scheme and rewrites `?` → `%s` for
  psycopg. App code uses `?` everywhere, so it is portable as-is.
- The memory graph traversal uses a standard `WITH RECURSIVE` CTE — valid on PostgreSQL.
- `ON CONFLICT(...) DO UPDATE` upserts are valid on PostgreSQL.
- Embedding BLOBs are raw `struct` float32 → store as `BYTEA`.

## Dialect deltas to apply (in a new `sql/00N_pg.sql`)
1. **Autoincrement.** `INTEGER PRIMARY KEY AUTOINCREMENT` → `BIGSERIAL PRIMARY KEY`
   (or `GENERATED ALWAYS AS IDENTITY`).
2. **Pragmas.** Drop the `PRAGMA` lines (WAL, foreign_keys, synchronous, busy_timeout);
   PostgreSQL equivalents are server config (`fsync`, `wal_level`) — leave at defaults.
3. **Timestamps.** `TEXT` ISO-8601 columns work, but prefer `TIMESTAMPTZ` with
   `now() AT TIME ZONE 'utc'`; keep ISO text if you want byte-identical audit payloads.
4. **BLOB.** `embedding BLOB` → `embedding BYTEA`.
5. **Append-only triggers.** Re-express the SQLite `BEFORE UPDATE/DELETE … RAISE(ABORT)`
   triggers as PL/pgSQL:
   ```sql
   CREATE OR REPLACE FUNCTION audit_no_mutation() RETURNS trigger AS $$
   BEGIN RAISE EXCEPTION 'audit_events is append-only'; END; $$ LANGUAGE plpgsql;
   CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events
     FOR EACH ROW EXECUTE FUNCTION audit_no_mutation();
   CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events
     FOR EACH ROW EXECUTE FUNCTION audit_no_mutation();
   ```
6. **Views.** `v_recent_violations`, `v_session_cost`, `v_rag_health`,
   `v_open_approvals` are plain SQL and port unchanged.

## Migration procedure (offline cutover)
1. **Provision.** `createdb claude_env`; set `PGDSN=postgresql://user@host/claude_env`.
2. **Schema.** Apply the PG schema:
   ```bash
   CLAUDE_ENV_DSN="$PGDSN" python - <<'PY'
   import sys; sys.path.insert(0,".")
   from lib.db import get_db
   get_db().apply_schema("sql/00N_pg.sql")   # PG-dialect schema + triggers + views
   PY
   ```
3. **Copy data, ledger first and in order.** Audit integrity depends on `event_id`
   order, so copy `audit_events` by ascending `event_id`, then the projection tables,
   then memory, then metrics:
   ```bash
   CLAUDE_ENV_SQLITE="sqlite:///$HOME/.claude-env/state/claude-env.db" \
   CLAUDE_ENV_PG="$PGDSN" python scripts/migrate_to_pg.py   # see note below
   ```
   (Implement `scripts/migrate_to_pg.py` as a straight row-copy using two `Database`
   instances; preserve `event_id`, `prev_hash`, `event_hash` verbatim — do **not**
   recompute hashes.)
4. **Verify the chain on PostgreSQL.**
   ```bash
   CLAUDE_ENV_DSN="$PGDSN" python - <<'PY'
   import sys; sys.path.insert(0,".")
   from audit.audit_logger import AuditLogger
   print(AuditLogger("migrate", actor="migrate").verify_chain())
   PY
   ```
   Must print `(True, None)`. If not, the copy reordered or altered rows — restart
   from a fresh PG schema.
5. **Sequence fix-up.** After copying explicit ids, advance the identity sequences:
   ```sql
   SELECT setval(pg_get_serial_sequence('audit_events','event_id'),
                 (SELECT MAX(event_id) FROM audit_events));
   -- repeat for every table copied with explicit ids
   ```
6. **Cutover.** Set `CLAUDE_ENV_DSN=$PGDSN` in the shell profile / launchd plists /
   MCP server env. Re-run the validators:
   ```bash
   for v in installation security memory agents; do
     CLAUDE_ENV_DSN="$PGDSN" python validation/validate_$v.py
   done
   ```
7. **LanceDB is unaffected.** Vectors stay in `~/.claude-env/knowledge/lancedb/`
   regardless of the SQL backend. For multi-machine, replicate the LanceDB dir or
   move it to shared storage.

## Rollback
The SQLite DB is untouched by the migration (read-only source). To roll back, set
`CLAUDE_ENV_DSN` back to the SQLite path and re-run validators. Keep the SQLite file
until the PostgreSQL deployment has run cleanly for a full backup cycle.

## Concurrency notes
- The audit writer takes a process-level lock (`_WRITE_LOCK`) for monotonic chaining.
  With multiple machines writing to one PostgreSQL ledger, replace this with a DB-level
  serialization: wrap the read-prev-hash + insert in a `SERIALIZABLE` transaction and
  retry on serialization failure, or funnel audit writes through a single writer
  service. Do not allow two processes to compute `prev_hash` concurrently.
- Memory and metrics tolerate concurrent writers as-is (last-write-wins / additive).
