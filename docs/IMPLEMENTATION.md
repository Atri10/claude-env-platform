# claude-env — Architecture & Subsystems

A privacy-first, local-only AI development platform for macOS Apple Silicon, centered
on Claude Code. This document describes the system's subsystems, their on-disk
artifacts, configuration, and operational semantics.

File paths refer to this repository; at install time `bootstrap.py` mirrors the code
under `~/.claude-env/` (`$CLAUDE_ENV_HOME`).

## Ground rules

- **Persistence is abstracted.** No module imports `sqlite3`/`psycopg` directly.
  Everything goes through `lib/db.py::get_db()`. SQLite is the default target
  (`CLAUDE_ENV_DSN=sqlite:///...`); PostgreSQL is a config change (see
  `docs/POSTGRES_MIGRATION.md`).
- **The audit ledger is the spine.** `audit_events` is append-only and hash-chained;
  DB triggers reject UPDATE/DELETE. Every other audit-like table is a typed projection
  written in the same transaction.
- **Policy is enforced at one chokepoint.** The `filesystem-policy` MCP server is the
  only sanctioned path to the filesystem for agents; nothing downstream re-checks
  policy, so that server is strict and fail-closed.
- **Retrieved/external text is data, never instructions.** RAG results and fetched docs
  are wrapped in `<retrieved_context>` / `<external_doc>` delimiters and screened by the
  poison/injection detectors.
- **No network egress by default.** Models run locally (llama.cpp + ONNX). The only
  outbound capability is the documentation fetch tool, restricted to tier-0/1.
- **Tiers (0 public → 3 highly restricted) tighten posture monotonically.** Higher tier
  = more deny rules, memory isolation, and (tier 3) default-deny on reads.

---

## Foundation

The host layout, persistence abstraction, and audit ledger that everything else writes
through.

**Components.** `bootstrap.py`, `lib/db.py`, `sql/001_schema.sql`,
`sql/002_retention.sql`, `audit/audit_logger.py`, `validation/validate_installation.py`.

**Folder structure under `$CLAUDE_ENV_HOME`.**
```
state/                 venv/                    <- isolated Python environment
knowledge/lancedb/     knowledge/docs/
models/                <- put downloaded model files here (no symlink); paths set in config/rag.yaml
archive/memory/        archive/audit/           archive/security/
logs/                  config/                  bin/                <- config/ holds rag.yaml
```

The venv at `$CLAUDE_ENV_HOME/venv/` is the **only** Python environment used to run
platform code; no global site-packages are touched. `bootstrap.py` creates it via the
stdlib `venv` module, then installs all deps inside it. The venv interpreter is
`$CLAUDE_ENV_HOME/venv/bin/python`.

**Configuration.** `CLAUDE_ENV_DSN` (default
`sqlite:///$HOME/.claude-env/state/claude-env.db`), `CLAUDE_ENV_HOME`. No secrets are
ever stored in config.

**Bootstrap usage.**
```bash
python3 bootstrap.py                   # full setup (creates venv, installs all deps)
python3 bootstrap.py --with-brew       # also install llama.cpp via Homebrew
python3 bootstrap.py --no-deps         # skip pip (venv must already exist)
python3 bootstrap.py --recreate-venv   # nuke and recreate venv (after Python upgrade)
python3 bootstrap.py --dsn postgresql://…

~/.claude-env/venv/bin/python validation/validate_installation.py
~/.claude-env/bin/claude-env validate installation
```

**Security.** Append-only triggers on `audit_events`; WAL mode; foreign keys on; DB
under `$CLAUDE_ENV_HOME/state` (`chmod 700 $CLAUDE_ENV_HOME` recommended).

**Reset.** Delete `$CLAUDE_ENV_HOME/state/claude-env.db` and re-run bootstrap (schema is
idempotent). Full reset: `rm -rf $CLAUDE_ENV_HOME` then bootstrap.

**PostgreSQL.** `init_database(--dsn postgresql://…)` applies the same DDL through the
abstraction; only `audit_events` triggers need the PG syntax in `POSTGRES_MIGRATION.md`.

---

## Security framework (audit + detectors)

Makes every action auditable and adds the heuristic detectors used at trust boundaries.

**Components.** `audit/audit_logger.py` (typed helpers: `agent_action`, `tool_call`,
`retrieval`, `memory_read`, `memory_write`, `security_event`, `policy_violation`,
`human_approval_request/resolve`, `verify_chain`); `security/detectors.py`
(`PromptInjectionDetector`, `SecretDetector`, `RagPoisonDetector`);
`validation/validate_security.py`.

**Configuration.** Detector thresholds are constructor args
(`PromptInjectionDetector(block_threshold=0.8)`).

**Security.** Tamper-evident hash chain
(`event_hash = sha256(prev_hash || canonical_json(payload))`); detectors log every flag
as a `security_events` row, so detection itself is auditable. Canonical-JSON hashing is
engine-independent; PostgreSQL keeps the identical chain.

**Notes.** Detectors are pure functions over text; disabling is a config/no-op. The
audit ledger is never rolled back (append-only); to retire, archive then start a fresh
DB (chain restarts from GENESIS, which is itself detectable).

---

## Repository guardrails (policy engine)

Decides, for any (repo, path, agent, tier), whether a file may be read and whether
content must be redacted.

**Components.** `security/policy_engine.py` (layered evaluation + content scan,
gitignore-style `**` globbing); `config/global-policy.yaml` (never-overridable global
deny + tier matrix); `config/repo-policy.template.yaml` (annotated per-repo schema).

**Per-repo files.** `<repo>/.claude/repo-policy.yaml`, optional
`<repo>/.claude/commands.json`, optional `<repo>/.claudeignore`.

**Policy schema (summary).** `version`, `tier`, `repo`, `allow{paths,extensions}`,
`deny{paths,extensions,regex[]}`, `content_scan{enabled,on_match,patterns[]}`,
`rag{enabled,index_paths,exclude_paths,index_only_committed}`,
`memory{namespace,isolated,share_with_agents}`,
`agent_permissions{<agent>:{write_paths,deny_tools,requires_approval}}`.

**Resolution order.** global deny → tier overrides → repo deny → repo allow →
fall-through (tier-3 = default-deny). Deny always wins.

- **Allow by default:** source, docs, ADRs, RFCs, tests.
- **Block by default:** `.env .pem .p12 .key .crt .cer`, `.ssh`, `.gnupg`, secrets,
  credentials, production configs, customer data, private exports, backups.

**Security.** Global deny cannot be overridden by a repo; the content scanner redacts or
blocks secrets before bytes leave the engine; tier-3 is default-deny. The engine is
stateless — edit/replace the YAML and it recompiles on load.

---

## Local RAG

Indexes repositories locally and serves hybrid, reranked retrieval with full provenance
and policy enforcement at index time.

**Components.**
```
rag/config.py                        rag/chunkers/chunkers.py
rag/embeddings/llama_embedder.py     rag/rerankers/cross_encoder.py
rag/retrievers/lance_store.py
rag/indexers/indexer.py              rag/pipelines/retrieve.py
rag/bootstrap_rag.py    rag/repository_scan.py    rag/incremental_index.py
rag/branch_index.py     rag/validate_rag.py
```

**Model selection.** No model is hardcoded in code — it is chosen at setup in
`config/rag.yaml`, resolved by `rag/config.py` (resolution order: env vars > rag.yaml >
numeric fallbacks). The built-in fallbacks are numeric only (ctx/dim/gpu); an
unconfigured `embedding.model_path` makes `get_embedder()` raise a clear error rather
than guess. RAG code never constructs an embedder/reranker directly — it calls
`rag.config.get_embedder()` / `get_reranker()`. Relevant env vars:

- `EMBED_MODEL_PATH` — path to the `.gguf` file (**required**; no symlink, point straight at it)
- `EMBED_MODEL_NAME` — optional label for `rag_index_state` (defaults to the filename)
- `EMBED_DOC_PREFIX`, `EMBED_QUERY_PREFIX` — optional per-task input prefixes
- `EMBED_CTX`, `EMBED_GPU_LAYERS`, `EMBED_DIM` — context, GPU offload, vector dim
- `RERANKER_DIR` — ONNX cross-encoder directory; empty/unset disables reranking
- `LANCEDB_PATH` — vector store location

See `docs/RUNBOOK.md` §2 for model download and configuration. Suggested settings (not
requirements): a 768-dim GGUF embedding model at Q8_0 (Q4/Q5 degrade recall); context
size 2048; chunk sizing 512 tokens (code) / 384 (markdown) / 256 (sections) with ~12%
overlap. Task prefixes are not inferred — set `document_prefix` / `query_prefix` in
`config/rag.yaml` if your model needs them.

**LanceDB layout.** `$CLAUDE_ENV_HOME/knowledge/lancedb/<repo>__<branch>.lance`, one
table per repo+branch; rows carry the vector plus all chunk metadata + tier; FTS index
on `text` for hybrid search. LanceDB is independent of the SQL backend.

**Usage.**
```bash
PY=~/.claude-env/venv/bin/python
$PY rag/repository_scan.py   /path/to/repo            # dry-run: allowed vs blocked
$PY rag/bootstrap_rag.py     /path/to/repo            # full index
$PY rag/incremental_index.py /path/to/repo --since HEAD~1
$PY rag/branch_index.py      /path/to/repo feature/x
$PY rag/validate_rag.py      /path/to/repo "test query"
# or via the CLI:
~/.claude-env/bin/claude-env scan  /path/to/repo
~/.claude-env/bin/claude-env index /path/to/repo
```

**Security.** The indexer runs every candidate path through the policy engine (blocked
paths logged as `policy_violations`); content scan redacts/blocks secrets before
embedding; only committed files are indexed when `index_only_committed: true`; retrieved
chunks are screened by `RagPoisonDetector` and wrapped as data.

**Re-indexing.** Incremental indexing is idempotent (content-hash keyed). To swap
embedding models: change `config/rag.yaml` (or `EMBED_MODEL_PATH` / `EMBED_DIM`), drop
the affected LanceDB tables and `rag_index_state` / `rag_file_state` rows, and re-index
— vectors from different models/dimensions are not comparable, so a full re-index is
required. The `embed_model` column records provenance per table. Gate swaps with
`claude-env rag-bench` (golden queries in `.claude/rag-eval.yaml`, recall@k + MRR).

**Feedback loop** (`observability/feedback.py` + `rag_chunk_feedback`). Every returned
chunk is recorded (`signal='retrieved'`); the session ingestor upgrades retrieval→edit
correlations to `signal='used'`. After reranking, chunks earn a bounded boost
(`0.05·ln(1+min(uses,20))`) — enough to re-order near-ties, never enough to overrule a
clear reranker decision. Disable with `CLAUDE_ENV_FEEDBACK_BOOST=false`.

**Unified recall** (`rag/pipelines/know.py`, `claude-env know`). Fuses memory recall
(repo + global namespaces, per-term fallback), RAG retrieval, and git log/grep into one
provenance-tagged answer; each source degrades independently (works before any model is
configured).

---

## Memory system

A local property graph (nodes + edges in SQLite) implementing episodic, semantic,
procedural, and agent memory with decay, consolidation, pruning, and namespace
isolation.

**Components.**
```
memory/memory_manager.py      memory/memory_retriever.py
memory/memory_consolidator.py memory/memory_pruner.py    memory/memory_validator.py
validation/validate_memory.py
```

**Memory types → storage.**
- episodic: `node_kind ∈ {session, decision, investigation}`
- semantic: `{entity, concept, architecture}`
- procedural: `{workflow, convention, pattern}` in namespace `global`
- agent: namespace `agent:<id>`

**Graph relationships.** `RELATES_TO`, `DEPENDS_ON`, `DECISION_ABOUT`, `DISCOVERED_IN`,
`SUPERSEDES`, `CONSOLIDATES`. Traversal uses a recursive CTE over `memory_edges` (the
Cypher-equivalent `expand()`). Recursive CTE is standard SQL and works on PostgreSQL;
embedding BLOBs are portable (`struct` float32).

**Confidence decay.** `effective = stored * 2^(-age_days / half_life_days)`; half-life
varies by kind (sessions 30d, decisions/architecture 365d, conventions 540d, …).

**Usage.**
```bash
PY=~/.claude-env/venv/bin/python
$PY memory/memory_validator.py    --all --repair
$PY memory/memory_consolidator.py --all
$PY memory/memory_pruner.py       --all            # dry-run
$PY memory/memory_pruner.py       --namespace proj-x --apply
```

**Security.** Namespace isolation (`isolated=True` refuses cross-namespace reads —
enforced for tier ≥ 2); append-only corrections via `supersede` (no destructive
overwrite); pruning archives to `archive/memory/<ns>.jsonl` before delete and never
prunes `decision`/`architecture`; all reads/writes audited. Consolidation marks sources
`superseded_by` rather than deleting, so it is reversible until a later prune.

**Self-population** (`memory/session_ingestor.py`, nightly). Claude Code transcripts
under `~/.claude/projects/` are parsed into one episodic `session` node each (task,
files touched/edited, tools used, outcome) — secret-redacted, deduped via
`session_ingest_state`. It also correlates RAG-retrieved files that were edited within
24h into `rag_chunk_feedback(signal='used')` rows, closing the retrieval feedback loop
(see Local RAG). Memory accrues with zero user discipline.

**Team sync** (`memory/memory_sync.py`, `claude-env memory-sync`). Namespace export to
JSONL with recursive secret redaction inside parsed JSON values (escaping-safe);
additive idempotent import with optional namespace remap; both directions audited.

---

## Multi-agent framework

The 10 specialists + orchestrator, with explicit permissions and deterministic routing,
handoff, conflict resolution, and approval gating.

**Components.**
```
agents/agent_registry.yaml
agents/prompts/{orchestrator,architect,backend,frontend,database,devops,
                security,performance,testing,documentation,research}.md
agents/orchestration/{task_router,agent_handoff,conflict_resolver,approval_gate}.py
validation/validate_agents.py
```

**Per-agent definition (in `agent_registry.yaml`).** prompt path, role,
`allowed_tools`, `denied_tools`, `memory_access`, `rag_access`, `write_paths`,
`requires_approval`, optional `tier_limits`. Examples: architect is read-only on source
+ memory write; backend writes `src/**`,`tests/**`; database writes
migrations/schema/db and never touches a live DB; devops `requires_approval: true`;
security read-only; testing mock-only; research web fetch only in tiers 0–1.

**Orchestration flow.** decompose → `task_router.route()` picks a specialist →
`approval_gate.evaluate()` (gate if needed) → `agent_handoff.handoff()` packages scoped
context as data → specialist acts via MCP → outputs reconciled by
`conflict_resolver.resolve()` → orchestrator synthesizes.

**Usage.**
```bash
PY=~/.claude-env/venv/bin/python
$PY agents/orchestration/task_router.py "add an /orders endpoint" --target src/api/orders.py
$PY agents/orchestration/approval_gate.py --list-open
$PY agents/orchestration/approval_gate.py --resolve appr-abc123 --approve --by alice
# or via the CLI:
~/.claude-env/bin/claude-env approvals --list-open
~/.claude-env/bin/claude-env route "add an /orders endpoint"
```

**Security.** Global approval gates (writes outside scope, state-mutating terminal, any
tier-2/3 action, git push/amend/rebase, memory delete/prune) + per-agent
`requires_approval`; handoffs narrow scope and flag out-of-scope paths; the conflict
resolver never silently drops a security/policy concern (it escalates; security veto
wins; true stalemate escalates). All orchestration is stateless except audit rows.

---

## MCP layer

The local MCP servers that are the agents' only interface to the filesystem, git, RAG,
memory, terminal, and docs.

**Components.**
```
config/mcp-servers.json
mcp-servers/filesystem-policy/server.py   mcp-servers/git/server.py
mcp-servers/lancedb-rag/server.py         mcp-servers/memory-graph/server.py
mcp-servers/terminal/server.py            mcp-servers/documentation/server.py
validation/validate_mcp.py
```

**Topology & startup order.** filesystem-policy (1) → git (2) → lancedb-rag (3) →
memory-graph (4) → terminal (5) → documentation (6) → jetbrains (7, IDE-provided,
optional). All run over **stdio**; none binds a network port.

**Permission scopes (per server).**
- **filesystem-policy:** `filesystem.read/write/list` (policy + content scan enforced,
  fail-closed). The single filesystem chokepoint; rejects path traversal; scans outgoing
  writes for secrets.
- **git:** read-only log/diff/blame/status/show; denies push/amend/rebase/reset.
- **lancedb-rag:** `lancedb.search` (read-only, data-wrapped, poison-screened).
- **memory-graph:** recall/read/expand/write/link with namespace isolation; no delete.
- **terminal:** allow-listed `run_tests/run_benchmarks/run_audit` only; `terminal.run`
  returns an approval directive; no unrestricted exec; scrubbed env + timeouts.
- **documentation:** local search always; external fetch only in tiers 0–1.

**Configuration.** `config/mcp-servers.json`; per-server env (`CLAUDE_ENV_REPO_ROOT`,
`CLAUDE_ENV_TIER`, `CLAUDE_ENV_MEMORY_NS`, `CLAUDE_ENV_MEMORY_ISOLATED`, `LANCEDB_PATH`).
Register with Claude Code via `claude mcp add` (see RUNBOOK). Servers import platform
code via `$CLAUDE_ENV_HOME`; a DB backend swap is transparent to them.

---

## Claude Code hooks enforcement

Closes the native-tool gap: MCP servers only govern MCP traffic, while Claude
Code's own Read/Write/Edit/Glob/Grep/Bash bypass them. The hooks make the same
policy engine govern everything.

**Components.** `hooks/policy_hook.py` (PreToolUse), `hooks/audit_hook.py`
(PostToolUse), `hooks/install_hooks.py` (idempotent `~/.claude/settings.json`
merge; `claude-env hooks`).

**Decision flow (PreToolUse).** incident marker → deny all; path policy block →
deny (+ `policy_violations` row); secret pattern in Write/Edit content → ask
(operator confirms, + `security_events` row); else allow silently. PostToolUse
writes `tool_calls` rows (`native.<Tool>`) for mutating calls
(`CLAUDE_ENV_HOOK_AUDIT_ALL=true` audits everything).

**Failure posture.** Internal errors fail OPEN by default so a broken hook can't
brick the editor; `CLAUDE_ENV_HOOK_FAIL_CLOSED=true` flips that for tier-2+
machines. Paths outside the enclosing repo are evaluated with the leading `/`
stripped so global `**/...` denies (e.g. `**/.ssh/**`) still match.

---

## Governance operations

**Incident mode** (`security/incident.py`, `claude-env incident on|off|status`).
A JSON marker at `$CLAUDE_ENV_HOME/state/INCIDENT` makes
`PolicyEngine.evaluate_path` fail closed on every surface at once (MCP server,
indexer, hooks). `on` also denies all pending approvals, snapshots the SQLite DB
(online `.backup`) to `archive/`, and writes a critical `security_event`; `off`
is audited.

**Compliance reports** (`audit/compliance_report.py`, `claude-env report`).
Windowed evidence from the ledger — events by type/actor, top tools, policy
violations, security events, approval trails, session costs — plus a fresh
`verify_chain()` integrity proof. md (human), csv (raw chained rows), json
(machine). Exit 1 when the chain is broken.

**Session replay** (`audit/session_replay.py`, `claude-env replay`). Orders a
session's ledger rows into a timeline with per-event-type one-line summaries;
`--list` enumerates recent sessions. Forensics without touching the DB by hand.

**Policy simulation** (`security/policy_sim.py`, `claude-env policy-sim`).
`simulate` evaluates a candidate repo policy against the real file tree next to
the current policy and reports newly blocked/allowed files (the engine is
stateless, so this is pure computation); `diff` is a structural drift check
against a baseline YAML.

**Cost budgets** (`observability/budgets.py` + `config/budgets.yaml`,
`claude-env budget`). Calendar-month spend per repo from `metrics_sessions`
vs configured USD limits; warn at a configurable fraction; exit 1 on exceed;
best-effort macOS notification.

---

## JetBrains integration

Wires Claude Code into the JetBrains IDEs with defined review/refactor/architecture/
debug workflows. See `docs/JETBRAINS.md` for the full procedure (plugin list, install
sequence, IDE settings, workflows) and the `jetbrains` entry in `config/mcp-servers.json`.

The IDE bridge respects the filesystem policy (it routes file ops through the same
chokepoint); no separate credential store. Disable it by removing the plugin / the
`jetbrains` server entry.

---

## Observability

Local, private metrics for latency, retrieval quality, token/cost, agent/tool activity,
and policy violations.

**Components.** `observability/collectors.py`, `observability/dashboard.py`, `metrics_*`
tables (from `sql/001_schema.sql`).

**Metrics schema.** `metrics_sessions` (tokens, est_cost_usd), `metrics_latency`
(component, operation, duration_ms), `metrics_retrieval_quality` (top1, mean_top_k,
rerank_delta).

**Usage.**
```bash
PY=~/.claude-env/venv/bin/python
$PY observability/dashboard.py                 # terminal summary
$PY observability/dashboard.py --window 7d
$PY observability/dashboard.py serve --port 8001   # datasette UI (read-only)
# or via the CLI:
~/.claude-env/bin/claude-env dashboard --window 7d
```

**Notes.** All metrics are local; the cost model is a configurable price table (update
`PRICES` to current pricing); datasette runs read-only on localhost. `metrics_*` tables
are rolling/prunable per `retention_policy`; truncating them loses only observability
data, never audit.

---

## Threat → control mapping

- **Prompt injection** → `PromptInjectionDetector` + data-delimited context +
  orchestrator "instructions only from operator" rule.
- **RAG poisoning** → `RagPoisonDetector.scan_chunk` at index and at serve time.
- **Secret exposure** → policy engine extension/path/regex deny + content scan (read
  *and* write) + `SecretDetector`.
- **Memory corruption** → `memory_validator` (dangling edges, bad confidence, supersede
  cycles, ns leakage) with `--repair`.
- **Policy bypass** → single filesystem chokepoint, path-traversal rejection,
  fail-closed — extended to Claude Code's native tools by the PreToolUse policy hook.
- **MCP over-permission** → explicit scopes + denied tools + allow-listed terminal.
- **Active compromise / runaway agent** → incident mode: one marker fails every
  policy evaluation closed across all surfaces, denies pending approvals, snapshots
  the DB (`claude-env incident on`).
- **Repo isolation** → per-repo policy + per-repo LanceDB tables + per-repo memory
  namespace.
- **Agent isolation** → registry permissions + approval gates + scoped handoffs.

**Full validation suite.**
```bash
PY=~/.claude-env/venv/bin/python
for v in installation security memory agents mcp; do
  $PY validation/validate_${v}.py || echo "FAILED: $v"
done
$PY rag/validate_rag.py /path/to/repo "test query"
# or in one shot:
~/.claude-env/bin/claude-env validate all
```

Additional hardening: `chmod 700 $CLAUDE_ENV_HOME`, FileVault assumed, ledger archival
with verification.

---

## Developer experience tools

All offline, stateless over the working tree + git unless noted.

- **`rag/context_pack.py`** (`claude-env context-pack`) — generates CLAUDE.md from
  repo signals (languages, layout, detected commands from
  Makefile/package.json/pyproject/go.mod/Cargo.toml) plus team memory (procedural
  conventions, recent decisions). Writes `CLAUDE.generated.md`; refuses to overwrite a
  hand-written `CLAUDE.md` without its generation marker.
- **`rag/test_impact.py`** (`claude-env test-impact`) — changed files → minimal test
  set via name-convention + import-reference heuristics; emits runnable commands
  (pytest/jest/go test).
- **`rag/doc_drift.py`** (`claude-env doc-drift`) — markdown→code references: broken
  paths, docs older than the code they describe (git commit times), optional
  `--semantic` similarity via the configured embedder.
- **`agents/analysts/nightly_analyst.py`** (`claude-env digest`, nightly per repo in
  `config/analyst-repos.txt`) — doc drift + TODO/FIXME aging (git blame) + possibly-dead
  top-level Python symbols + size hotspots → `logs/digests/<repo>-<date>.md` and an
  episodic `investigation` memory node, so the next session starts knowing repo health.
- **`agents/orchestration/approvals_ui.py`** (`claude-env approvals-ui`) — stdlib-only
  localhost page with one-click audited approve/deny (per-process CSRF token,
  127.0.0.1 bind); gate opens raise a macOS notification.

## Packaging & platforms

**Claude Code plugin** (`claude-plugin/`). One installable artifact wiring the hooks
(`hooks/hooks.json`), the four core MCP servers (`.mcp.json`), and slash commands
(`commands/know|report|replay.md`). The platform itself must be bootstrapped on the
machine; the plugin is the distribution layer for fleets.

**Linux.** Bootstrap is platform-aware (Metal flag only on Darwin/arm64; brew advisory
on Linux); scheduling uses systemd user units (`scripts/systemd/`,
`claude-env-nightly.timer` at 03:15) instead of launchd; `nightly_memory.sh` is bash.

---

## Backup, recovery & upgrades

**Backup.** SQLite: `sqlite3 claude-env.db ".backup '…'"` (online, WAL-safe) on a
schedule. LanceDB: rsync the `lancedb/` dir. Memory archive JSONL is a secondary
recovery source.

**Recovery.** Restore DB + LanceDB dir, then run
`~/.claude-env/venv/bin/python validation/validate_installation.py` and
`AuditLogger.verify_chain()`. Keep the prior DB snapshot to restore on a failed upgrade.

**Upgrades.** Schema: bump `schema_version`, add `sql/00N_*.sql`, apply via
`db.apply_schema()`, re-run validators. Code: pull, then
`python3 bootstrap.py --no-deps` (recreates the code mirror under `$CLAUDE_ENV_HOME`
without rebuilding the venv). Packages:
`~/.claude-env/venv/bin/pip install --upgrade <pkg>` or
`python3 bootstrap.py --no-venv-create`.

**PostgreSQL migration.** `docs/POSTGRES_MIGRATION.md` gives the exact cutover.
