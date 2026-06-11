# claude-env — Implementation Blueprint

A privacy-first, local-only AI development platform for macOS Apple Silicon,
centered on Claude Code. This document is the executable plan: a senior engineer
should be able to build the platform phase by phase without making architectural
decisions. The architecture is already approved; what follows is *how to build it*.

Every phase below specifies: objective, estimated effort, dependencies,
deliverables, folder structure, configuration, scripts, validation, security
controls, rollback, exit/acceptance criteria, and forward-migration notes. File
paths refer to this repository; at install time `bootstrap.py` mirrors the code
under `~/.claude-env/` (referred to as `$CLAUDE_ENV_HOME`).

---

## 0. Conventions and ground rules

- **Persistence is abstracted.** No module imports `sqlite3`/`psycopg` directly.
  Everything goes through `lib/db.py::get_db()`. SQLite is the default target
  (`CLAUDE_ENV_DSN=sqlite:///...`); PostgreSQL is a config change (Phase 10 /
  `docs/POSTGRES_MIGRATION.md`).
- **The audit ledger is the spine.** `audit_events` is append-only and
  hash-chained; DB triggers reject UPDATE/DELETE. Every other audit-like table is
  a typed projection written in the same transaction.
- **Policy is enforced at one chokepoint.** The `filesystem-policy` MCP server is
  the only sanctioned path to the filesystem for agents; nothing downstream
  re-checks policy, so that server is strict and fail-closed.
- **Retrieved/external text is data, never instructions.** RAG results and fetched
  docs are wrapped in `<retrieved_context>` / `<external_doc>` delimiters and
  screened by the poison/injection detectors.
- **No network egress by default.** Models run locally (llama.cpp + ONNX). The
  only outbound capability is the documentation fetch tool, restricted to tier-0/1.
- **Tiers (0 public → 3 highly restricted) tighten posture monotonically.** Higher
  tier = more deny rules, memory isolation, and (tier 3) default-deny on reads.

Effort is given in engineer-days for one experienced engineer. Total ≈ **24–34 days**.

---

## Phase 0 — Foundation

**Objective.** Stand up the host, the directory layout, persistence, and the audit
ledger so every later phase has a place to write and an auditable trail.

**Estimated effort.** 2–3 days.
**Dependencies.** None.

**Deliverables.**
- `bootstrap.py` (env checks, venv creation, dirs, deps into venv, DB init, LanceDB init, policy copy, audit genesis, validate via venv Python)
- `lib/db.py` (persistence abstraction)
- `sql/001_schema.sql`, `sql/002_retention.sql`
- `audit/audit_logger.py`
- `validation/validate_installation.py`

**Folder structure created under `$CLAUDE_ENV_HOME`.**
```
state/                 venv/                    <- isolated Python environment
knowledge/lancedb/     knowledge/docs/
models/embedding/      models/reranker-onnx/
archive/memory/        archive/audit/           archive/security/
logs/                  config/                  bin/
```

The venv lives at `$CLAUDE_ENV_HOME/venv/` and is the **only** Python environment
used to run platform code. No global site-packages are touched. `bootstrap.py` uses
the stdlib `venv` module (no pip pre-condition beyond what ships with Python) to
create it, then installs all deps inside it. The venv interpreter is:
`$CLAUDE_ENV_HOME/venv/bin/python`.

**Configuration.** `CLAUDE_ENV_DSN` (default `sqlite:///$HOME/.claude-env/state/claude-env.db`),
`CLAUDE_ENV_HOME`. No secrets are ever stored in config.

**Scripts / usage.**
```bash
# bootstrap always invoked with any Python ≥ 3.13; it creates the venv internally
python3 bootstrap.py                   # full setup (creates venv, installs all deps)
python3 bootstrap.py --with-brew       # also install llama.cpp via Homebrew
python3 bootstrap.py --no-deps         # skip pip (venv must already exist)
python3 bootstrap.py --recreate-venv   # nuke and recreate venv (after Python upgrade)
python3 bootstrap.py --dsn postgresql://…

# after bootstrap, use the venv Python or the CLI (which auto-resolves the venv)
~/.claude-env/venv/bin/python validation/validate_installation.py
~/.claude-env/bin/claude-env validate installation
```

**Validation.** `~/.claude-env/venv/bin/python validation/validate_installation.py`
(or `claude-env validate installation`) — checks Python ≥3.13, venv exists and runs,
directory layout, all 16 tables present, policy files installed, audit chain verifies.

**Security controls.** Append-only triggers on `audit_events`; WAL mode; foreign
keys on; DB lives under `$CLAUDE_ENV_HOME/state` (user-only perms recommended:
`chmod 700 $CLAUDE_ENV_HOME`).

**Rollback.** Delete `$CLAUDE_ENV_HOME/state/claude-env.db` and re-run bootstrap
(schema is idempotent). To fully reset: `rm -rf $CLAUDE_ENV_HOME` then bootstrap.

**Exit / acceptance criteria.**
- `validate_installation.py` exits 0 (soft warnings allowed for optional deps).
- `$CLAUDE_ENV_HOME/venv/bin/python --version` runs and reports ≥ 3.13.
- `AuditLogger.verify_chain()` returns `(True, None)`.
- A manual `UPDATE`/`DELETE` on `audit_events` raises and is rejected.

**Future migration.** `init_database(--dsn postgresql://…)` applies the same DDL
through the abstraction; only `audit_events` triggers need the PG syntax in
`POSTGRES_MIGRATION.md`.

---

## Phase 1 — Security Framework (audit + detectors)

**Objective.** Make every action auditable and add the heuristic detectors used at
trust boundaries.

**Estimated effort.** 2 days.
**Dependencies.** Phase 0.

**Deliverables.**
- `audit/audit_logger.py` typed helpers: `agent_action`, `tool_call`, `retrieval`,
  `memory_read`, `memory_write`, `security_event`, `policy_violation`,
  `human_approval_request/resolve`, `verify_chain`.
- `security/detectors.py`: `PromptInjectionDetector`, `SecretDetector`,
  `RagPoisonDetector`.
- `validation/validate_security.py`.

**Configuration.** Detector thresholds are constructor args
(`PromptInjectionDetector(block_threshold=0.8)`).

**Validation.** `python validation/validate_security.py` — exercises blocks/allows,
content redaction, injection/secret/poison detection, and append-only enforcement.

**Security controls.** Tamper-evident hash chain
(`event_hash = sha256(prev_hash || canonical_json(payload))`); detectors log every
flag as a `security_events` row so detection itself is auditable.

**Rollback.** Detectors are pure functions over text; disabling is a config/no-op.
The audit ledger is never rolled back (append-only); to retire, archive then start a
fresh DB (chain restarts from GENESIS, which is itself detectable).

**Exit / acceptance criteria.** `validate_security.py` exits 0; planted injection and
secrets are blocked; ledger mutation is rejected.

**Future migration.** Canonical-JSON hashing is engine-independent; PG keeps the
identical chain.

---

## Phase 2 — Repository Guardrails (policy engine)

**Objective.** Decide, for any (repo, path, agent, tier), whether a file may be read
and whether content must be redacted — the core guardrail.

**Estimated effort.** 2–3 days.
**Dependencies.** Phase 1.

**Deliverables.**
- `security/policy_engine.py` (layered evaluation + content scan, gitignore-style
  `**` globbing).
- `config/global-policy.yaml` (never-overridable global deny + tier matrix).
- `config/repo-policy.template.yaml` (annotated per-repo schema).

**Folder structure (per repo).** `<repo>/.claude/repo-policy.yaml`,
optional `<repo>/.claude/commands.json`, optional `<repo>/.claudeignore`.

**Policy schema (summary).** `version`, `tier`, `repo`, `allow{paths,extensions}`,
`deny{paths,extensions,regex[]}`, `content_scan{enabled,on_match,patterns[]}`,
`rag{enabled,index_paths,exclude_paths,index_only_committed}`,
`memory{namespace,isolated,share_with_agents}`,
`agent_permissions{<agent>:{write_paths,deny_tools,requires_approval}}`.

**Resolution order.** global deny → tier overrides → repo deny → repo allow →
fall-through (tier-3 = default-deny). Deny always wins.

**Allow by default:** source, docs, ADRs, RFCs, tests.
**Block by default:** `.env .pem .p12 .key .crt .cer`, `.ssh`, `.gnupg`, secrets,
credentials, production configs, customer data, private exports, backups.

**Validation.** Covered by `validate_security.py` (block/allow/redact matrix).

**Security controls.** Global deny cannot be overridden by a repo; content scanner
redacts or blocks secrets before bytes leave the engine; tier-3 is default-deny.

**Rollback.** Edit/replace the YAML; the engine recompiles on load. No state to undo.

**Exit / acceptance criteria.** The must-block set is blocked and the benign set is
allowed for tiers 0–3; AWS keys in content are redacted.

**Future migration.** None (engine is stateless).

---

## Phase 3 — Local RAG

**Objective.** Index repositories locally and serve hybrid, reranked retrieval with
full provenance and policy enforcement at index time.

**Estimated effort.** 4–5 days.
**Dependencies.** Phases 0–2.

**Deliverables.**
```
rag/embeddings/llama_embedder.py     rag/chunkers/chunkers.py
rag/retrievers/lance_store.py        rag/rerankers/cross_encoder.py
rag/indexers/indexer.py              rag/pipelines/retrieve.py
rag/bootstrap_rag.py    rag/repository_scan.py    rag/incremental_index.py
rag/branch_index.py     rag/validate_rag.py
```

**Embedding recommendations (with reasoning).**
- Model: **nomic-embed-text-v1.5 (GGUF, Q8_0)** — 768-dim, strong retrieval,
  fully local, Metal-friendly, small.
- Context size: **2048** (max chunk ~512 tokens + prefix + batching headroom).
- Quantization: **Q8_0** for embeddings — Q4/Q5 degrade recall; reserve those for
  generation models.
- Chunk sizing: **512** tokens (code) / **384** (markdown) / **256** (ADR/RFC sections).
- Chunk overlap: **64 / 48 / 32** (~12%) — preserves cross-boundary context without
  index bloat.

**LanceDB layout.** `$CLAUDE_ENV_HOME/knowledge/lancedb/<repo>__<branch>.lance`, one
table per repo+branch; rows carry the vector plus all chunk metadata + tier; FTS
index on `text` for hybrid search.

**Configuration.** `LANCEDB_PATH`, `RERANKER_DIR`, model path in
`rag/embeddings/llama_embedder.py::DEFAULTS`.

**Scripts / usage.**
```bash
PY=~/.claude-env/venv/bin/python   # set once; use throughout
$PY rag/repository_scan.py   /path/to/repo            # dry-run: allowed vs blocked
$PY rag/bootstrap_rag.py     /path/to/repo            # full index
$PY rag/incremental_index.py /path/to/repo --since HEAD~1
$PY rag/branch_index.py      /path/to/repo feature/x
$PY rag/validate_rag.py      /path/to/repo "test query"
# or via the CLI:
~/.claude-env/bin/claude-env scan  /path/to/repo
~/.claude-env/bin/claude-env index /path/to/repo
```

**Validation.** `validate_rag.py` checks index state present, chunk_count > 0, and a
live query returns hits.

**Security controls.** Indexer runs every candidate path through the policy engine
(blocked paths logged as `policy_violations`); content scan redacts/blocks secrets
before embedding; only committed files indexed when `index_only_committed: true`;
retrieved chunks are screened by `RagPoisonDetector` and wrapped as data.

**Rollback.** Drop the LanceDB table directory for a repo/branch and delete its rows
from `rag_index_state` / `rag_file_state`; re-run `bootstrap_rag.py`. Incremental
indexing is idempotent (content-hash keyed).

**Exit / acceptance criteria.** `validate_rag.py` exits 0 on a sample repo; blocked
files never appear in results; re-indexing an unchanged file is a no-op.

**Future migration.** Bookkeeping tables move with the DB; LanceDB is independent of
the SQL backend. To swap embedding models, re-index (the `embed_model` column records
provenance).

---

## Phase 4 — Memory System

**Objective.** A local property graph (nodes + edges in SQLite) implementing
episodic, semantic, procedural, and agent memory with decay, consolidation,
pruning, and namespace isolation.

**Estimated effort.** 3–4 days.
**Dependencies.** Phases 0–1 (and Phase 3 embedder for semantic recall, optional).

**Deliverables.**
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

**Graph relationships.** `RELATES_TO`, `DEPENDS_ON`, `DECISION_ABOUT`,
`DISCOVERED_IN`, `SUPERSEDES`, `CONSOLIDATES`. Traversal uses a recursive CTE over
`memory_edges` (the Cypher-equivalent `expand()`).

**Confidence decay.** `effective = stored * 2^(-age_days / half_life_days)`; half-life
varies by kind (sessions 30d, decisions/architecture 365d, conventions 540d, …).

**Scripts / usage.**
```bash
PY=~/.claude-env/venv/bin/python
$PY memory/memory_validator.py    --all --repair
$PY memory/memory_consolidator.py --all
$PY memory/memory_pruner.py       --all            # dry-run
$PY memory/memory_pruner.py       --namespace proj-x --apply
```

**Validation.** `validate_memory.py` — round-trip, graph expand, keyword recall,
supersede semantics, namespace isolation, decay, integrity.

**Security controls.** Namespace isolation (`isolated=True` refuses cross-namespace
reads — enforced for tier ≥ 2); append-only corrections via `supersede` (no
destructive overwrite); pruning archives to JSONL before delete and never prunes
`decision`/`architecture`; all reads/writes audited.

**Rollback.** Pruner archives to `archive/memory/<ns>.jsonl`; restore by re-inserting.
Consolidation marks sources `superseded_by` rather than deleting, so it is reversible
until a later prune.

**Exit / acceptance criteria.** `validate_memory.py` exits 0; isolated namespaces show
no leakage; pruning protects decisions; validator reports a clean graph.

**Future migration.** Recursive CTE is standard SQL and works on PostgreSQL; embedding
BLOBs are portable (`struct` float32). For very large graphs, the same schema maps to
a dedicated graph store later without changing the API surface.

---

## Phase 5 — Multi-Agent Framework

**Objective.** Define the 10 specialists + orchestrator with explicit permissions and
implement deterministic routing, handoff, conflict resolution, and approval gating.

**Estimated effort.** 3–4 days.
**Dependencies.** Phases 1–2 (audit + policy), Phase 4 (memory) recommended.

**Deliverables.**
```
agents/agent_registry.yaml
agents/prompts/{orchestrator,architect,backend,frontend,database,devops,
                security,performance,testing,documentation,research}.md
agents/orchestration/{task_router,agent_handoff,conflict_resolver,approval_gate}.py
validation/validate_agents.py
```

**Per-agent definition (in `agent_registry.yaml`).** prompt path, role,
`allowed_tools`, `denied_tools`, `memory_access`, `rag_access`, `write_paths`,
`requires_approval`, optional `tier_limits`. Examples: architect is read-only on
source + memory write; backend writes `src/**`,`tests/**`; database writes
migrations/schema/db and never touches a live DB; devops `requires_approval: true`;
security read-only; testing mock-only; research web fetch only in tiers 0–1.

**Orchestration flow.** decompose → `task_router.route()` picks a specialist →
`approval_gate.evaluate()` (gate if needed) → `agent_handoff.handoff()` packages
scoped context as data → specialist acts via MCP → outputs reconciled by
`conflict_resolver.resolve()` → orchestrator synthesizes.

**Scripts / usage.**
```bash
PY=~/.claude-env/venv/bin/python
$PY agents/orchestration/task_router.py "add an /orders endpoint" --target src/api/orders.py
$PY agents/orchestration/approval_gate.py --list-open
$PY agents/orchestration/approval_gate.py --resolve appr-abc123 --approve --by alice
# or via the CLI:
~/.claude-env/bin/claude-env approvals --list-open
~/.claude-env/bin/claude-env route "add an /orders endpoint"
```

**Validation.** `validate_agents.py` — registry completeness, prompt files exist,
router correctness on representative tasks, approval-gate firing matrix, conflict
precedence (security veto) and stalemate escalation.

**Security controls.** Global approval gates (writes outside scope, state-mutating
terminal, any tier-2/3 action, git push/amend/rebase, memory delete/prune) +
per-agent `requires_approval`; handoffs narrow scope and flag out-of-scope paths;
conflict resolver never silently drops a security/policy concern (escalates).

**Rollback.** All orchestration is stateless except audit rows. Reverting a routing
or gate change is a YAML/code edit. Pending approvals can be denied to unblock.

**Exit / acceptance criteria.** `validate_agents.py` exits 0; no agent can be routed a
write outside its `write_paths` without a gate; security veto wins; true stalemate
escalates.

**Future migration.** None (config + stateless logic).

---

## Phase 6 — MCP Layer

**Objective.** Deploy the local MCP servers that are the agents' only interface to the
filesystem, git, RAG, memory, terminal, and docs.

**Estimated effort.** 4–5 days.
**Dependencies.** Phases 2–5.

**Deliverables.**
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

**Permission scopes (per server).** filesystem-policy: `filesystem.read/write/list`
(policy + content scan enforced, fail-closed); git: read-only log/diff/blame/status/
show, denies push/amend/rebase/reset; lancedb-rag: `lancedb.search` (read-only, data-
wrapped, poison-screened); memory-graph: recall/read/expand/write/link with namespace
isolation, no delete; terminal: allow-listed `run_tests/run_benchmarks/run_audit`
only, `terminal.run` returns an approval directive, no unrestricted exec;
documentation: local search always, external fetch only tiers 0–1.

**Configuration.** `config/mcp-servers.json`; per-server env (`CLAUDE_ENV_REPO_ROOT`,
`CLAUDE_ENV_TIER`, `CLAUDE_ENV_MEMORY_NS`, `CLAUDE_ENV_MEMORY_ISOLATED`,
`LANCEDB_PATH`). Register with Claude Code via `claude mcp add` (see RUNBOOK).

**Validation.** `validate_mcp.py` — config parses, server files exist, startup order
unique with filesystem-policy first, security posture flags present, and (with `mcp`
installed) each module imports and exposes `server`.

**Security controls.** Single filesystem chokepoint; path-traversal rejection;
outgoing-write secret scan; scrubbed env + timeouts for terminal; tier-gated doc
fetch; everything audited.

**Rollback.** Remove a server from `config/mcp-servers.json` and re-register; servers
are stateless processes.

**Exit / acceptance criteria.** `validate_mcp.py` exits 0; an agent read of a blocked
path returns BLOCKED and writes a `policy_violations` row; `terminal.run` is gated.

**Future migration.** Servers import platform code via `$CLAUDE_ENV_HOME`; DB backend
swap is transparent to them.

---

## Phase 7 — JetBrains Integration

**Objective.** Wire Claude Code into the JetBrains IDEs with defined review/refactor/
architecture/debug workflows. See `docs/JETBRAINS.md` for the full procedure.

**Estimated effort.** 1–2 days.
**Dependencies.** Phase 6.

**Deliverables.** `docs/JETBRAINS.md` (plugin list, install sequence, IDE settings,
four workflows), `jetbrains` entry in `config/mcp-servers.json`.

**Validation.** Manual smoke test per IDE (IntelliJ, PyCharm, WebStorm, GoLand, Rider,
CLion): Claude Code reads current-file context, runs an inspection, proposes a
refactor that respects `filesystem-policy`.

**Security controls.** The IDE bridge respects the filesystem policy (it routes file
ops through the same chokepoint); no separate credential store.

**Rollback.** Disable the plugin / remove the `jetbrains` server entry.

**Exit / acceptance criteria.** Each target IDE can invoke Claude Code and retrieve
RAG context; a blocked file is not surfaced.

**Future migration.** None.

---

## Phase 8 — Observability

**Objective.** Local, private metrics for latency, retrieval quality, token/cost,
agent/tool activity, and policy violations.

**Estimated effort.** 1–2 days.
**Dependencies.** Phase 0 (schema), producers across Phases 3–6.

**Deliverables.** `observability/collectors.py`, `observability/dashboard.py`,
`metrics_*` tables (from `sql/001_schema.sql`).

**Metrics schema.** `metrics_sessions` (tokens, est_cost_usd), `metrics_latency`
(component, operation, duration_ms), `metrics_retrieval_quality` (top1, mean_top_k,
rerank_delta).

**Scripts / usage.**
```bash
PY=~/.claude-env/venv/bin/python
$PY observability/dashboard.py                 # terminal summary
$PY observability/dashboard.py --window 7d
$PY observability/dashboard.py serve --port 8001   # datasette UI (read-only)
# or via the CLI:
~/.claude-env/bin/claude-env dashboard --window 7d
```

**Validation.** Generate activity (run validators / a retrieval), then confirm the
dashboard reports non-empty latency and security sections.

**Security controls.** All metrics local; cost model is a configurable price table
(update `PRICES` to current pricing); datasette runs read-only on localhost.

**Rollback.** `metrics_*` tables are rolling/prunable per `retention_policy`; truncating
them loses only observability data, never audit.

**Exit / acceptance criteria.** Dashboard renders; latency p50/p95 populate after a
retrieval; security events surface.

**Future migration.** Tables move with the DB; for long histories, point datasette /
collectors at the PostgreSQL DSN.

---

## Phase 9 — Hardening

**Objective.** Validate the full defensive posture end to end and close gaps.

**Estimated effort.** 2–3 days.
**Dependencies.** Phases 1–8.

**Deliverables.** Detectors wired into MCP servers (done in Phase 6), the full
validation suite, and a documented threat-to-control mapping (below + RUNBOOK).

**Threat → control.**
- Prompt injection → `PromptInjectionDetector` + data-delimited context + orchestrator
  "instructions only from operator" rule.
- RAG poisoning → `RagPoisonDetector.scan_chunk` at index and at serve time.
- Secret exposure → policy engine extension/path/regex deny + content scan (read *and*
  write) + `SecretDetector`.
- Memory corruption → `memory_validator` (dangling edges, bad confidence, supersede
  cycles, ns leakage) with `--repair`.
- Policy bypass → single filesystem chokepoint, path-traversal rejection, fail-closed.
- MCP over-permission → explicit scopes + denied tools + allow-listed terminal.
- Repo isolation → per-repo policy + per-repo LanceDB tables + per-repo memory namespace.
- Agent isolation → registry permissions + approval gates + scoped handoffs.

**Validation.** Run the whole suite:
```bash
PY=~/.claude-env/venv/bin/python
for v in installation security memory agents mcp; do
  $PY validation/validate_${v}.py || echo "FAILED: $v"
done
$PY rag/validate_rag.py /path/to/repo "test query"
# or in one shot:
~/.claude-env/bin/claude-env validate all
```

**Security controls.** As above; plus `chmod 700 $CLAUDE_ENV_HOME`, FileVault assumed,
and ledger archival with verification.

**Rollback.** Hardening adds checks, not state; revert individual detector wiring if a
false-positive rate is unacceptable (tune thresholds first).

**Exit / acceptance criteria.** All six validators exit 0 (soft warnings allowed only
for optional model deps); the threat table has a tested control per row.

**Future migration.** None.

---

## Phase 10 — Operational Readiness

**Objective.** Make the platform operable: runbook, deployment checklist, backup/
recovery, upgrades, and the PostgreSQL migration plan.

**Estimated effort.** 2–3 days.
**Dependencies.** Phases 0–9.

**Deliverables.** `docs/RUNBOOK.md`, `docs/DEPLOYMENT_CHECKLIST.md`,
`docs/POSTGRES_MIGRATION.md`, `scripts/post-commit`, `scripts/nightly_memory.sh`,
`scripts/launchd.README.md`, `scripts/.claudeignore.template`.

**Backup & recovery (summary).** SQLite: `sqlite3 claude-env.db ".backup '…'"` (online,
WAL-safe) on a schedule; LanceDB: rsync the `lancedb/` dir; memory archive JSONL is a
secondary recovery source. Recovery = restore DB + LanceDB dir, then
`~/.claude-env/venv/bin/python validation/validate_installation.py`
and `AuditLogger.verify_chain()`.

**Upgrades (summary).** Bump `schema_version`, add `sql/00N_*.sql`, apply via
`db.apply_schema()`, re-run validators. Code upgrades: pull, re-run
`python3 bootstrap.py --no-deps` (recreates the code mirror under `$CLAUDE_ENV_HOME`
without rebuilding the venv). To upgrade the venv's packages:
`~/.claude-env/venv/bin/pip install --upgrade <pkg>` or
`python3 bootstrap.py --no-venv-create` (re-runs pip inside the existing venv).

**Validation.** Dry-run a backup+restore into a temp `$CLAUDE_ENV_HOME` and confirm the
audit chain verifies post-restore.

**Security controls.** Backups inherit FileVault; archived audit retains hashes for
post-hoc verification.

**Rollback.** Keep the prior DB snapshot; restore on a failed upgrade.

**Exit / acceptance criteria.** A clean machine can be brought to "all validators green"
using only `docs/DEPLOYMENT_CHECKLIST.md`; a restore drill verifies the chain.

**Future migration.** `docs/POSTGRES_MIGRATION.md` gives the exact cutover.

---

## Build order summary

| Phase | Name | Effort (d) | Hard deps |
|------:|------|:----------:|-----------|
| 0 | Foundation | 2–3 | — |
| 1 | Security Framework | 2 | 0 |
| 2 | Repository Guardrails | 2–3 | 1 |
| 3 | Local RAG | 4–5 | 0,1,2 |
| 4 | Memory System | 3–4 | 0,1 |
| 5 | Multi-Agent Framework | 3–4 | 1,2,(4) |
| 6 | MCP Layer | 4–5 | 2,3,4,5 |
| 7 | JetBrains Integration | 1–2 | 6 |
| 8 | Observability | 1–2 | 0 |
| 9 | Hardening | 2–3 | 1–8 |
| 10 | Operational Readiness | 2–3 | 0–9 |

Phases 3, 4, and 8 can proceed in parallel once Phases 0–2 are complete.
