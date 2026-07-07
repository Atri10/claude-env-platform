# claude-env

**A privacy-first, local-only AI development platform for Claude Code.** Everything
runs on your machine — local embeddings and reranking (llama.cpp + ONNX), a local
LanceDB vector store, a local SQLite memory graph, and a tamper-evident local audit
ledger. No code, embeddings, or telemetry ever leave the host. Primary target is macOS
Apple Silicon (Metal-accelerated); Linux is supported (CPU inference + systemd timers).

This document is the complete guide — read it top to bottom the first time, then use the
table of contents as a reference.

---
## Table of contents

**Part I — Overview**

1. [What it is](#1-what-it-is)
2. [Feature map](#2-feature-map)

**Part II — Architecture**

3. [Invariants (the ground rules)](#3-invariants-the-ground-rules)
4. [System topology](#4-system-topology)
5. [Subsystems](#5-subsystems)
6. [Threat → control mapping](#6-threat--control-mapping)
7. [On-disk layout](#7-on-disk-layout)

**Part III — Installation**

8. [Prerequisites](#8-prerequisites)
9. [Bootstrap](#9-bootstrap)
10. [Local models](#10-local-models)
11. [Native-tool hooks](#11-native-tool-hooks)

**Part IV — Onboarding a repository**

12. [One-step onboarding](#12-one-step-onboarding)
13. [What lands in the repo](#13-what-lands-in-the-repo)
14. [Index, verify, auto-reindex](#14-index-verify-auto-reindex)
15. [repo-policy.yaml reference](#15-repo-policyyaml-reference)
16. [Register MCP servers (optional)](#16-register-mcp-servers-optional)

**Part V — Operations**

17. [Everyday verification](#17-everyday-verification)
18. [Memory maintenance](#18-memory-maintenance)
19. [Approvals](#19-approvals)
20. [Observability & budgets](#20-observability--budgets)
21. [Governance: compliance, replay, incident, policy-sim](#21-governance-compliance-replay-incident-policy-sim)
22. [Knowledge & quality tools](#22-knowledge--quality-tools)
23. [Team knowledge sync](#23-team-knowledge-sync)
24. [Backup & recovery](#24-backup--recovery)
25. [Upgrades](#25-upgrades)
26. [Nightly automation](#26-nightly-automation)

**Part VI — Reference**

27. [CLI command reference](#27-cli-command-reference)
28. [Troubleshooting](#28-troubleshooting)
29. [Appendix A — JetBrains integration](#appendix-a--jetbrains-integration)
30. [Appendix B — PostgreSQL migration](#appendix-b--postgresql-migration)
31. [Appendix C — Plugin distribution](#appendix-c--plugin-distribution)
32. [License & use](#license--use)

---

# Part I — Overview

## 1. What it is

claude-env wraps Claude Code in a **local governance layer**. Every file read, command
run, retrieval, and memory write is policy-checked, audited to a tamper-evident ledger,
and scoped to the repository it belongs to — without any of it leaving the machine.

The design goals, in priority order:

- **Privacy** — local inference only; no network egress by default (the sole outbound
  capability is a tier-gated documentation fetch).
- **Least privilege** — agents and tools get explicit, narrow permissions; anything
  outside scope is denied or routed to a human approval gate.
- **Auditability** — an append-only, hash-chained ledger records every action and can be
  proven intact (`verify_chain()`).
- **Isolation** — each repo gets its own policy, its own RAG index, and its own memory
  namespace.

Everything runs through a single Python virtual environment at `$CLAUDE_ENV_HOME/venv/`
(default `~/.claude-env/venv/`). The system Python — used once to run `bootstrap.py` — is
never modified. The CLI, the MCP servers, the git hook, and the nightly jobs all invoke
`$CLAUDE_ENV_HOME/venv/bin/python` explicitly, so you never activate the venv by hand.

## 2. Feature map

| Area | What you get |
|---|---|
| **Guardrails** | Layered policy engine (global deny → tier → repo deny → repo allow; deny wins; tier-3 is default-deny) + content secret-scanning. Source/docs/tests allowed by default; secrets/keys/prod-configs/PII blocked. |
| **Audit** | Append-only, hash-chained `audit_events`; DB triggers reject any mutation; `verify_chain()` proves integrity. |
| **Local RAG** | AST-aware chunking, pluggable local embeddings + optional ONNX cross-encoder rerank, LanceDB hybrid search, one table per repo+branch, incremental re-index on commit. No model is baked into the code — you pick it at setup. |
| **Memory graph** | Episodic / semantic / procedural / agent memory as nodes+edges in SQLite, with confidence decay, supersede-based corrections, consolidation, pruning, and namespace isolation. |
| **Agents** | 10 specialists + orchestrator with explicit tools, write scopes, and approval gates; deterministic routing, scoped handoffs, conflict resolver with security veto. |
| **MCP layer** | Six local stdio servers; the policy-enforcing filesystem server is the single sanctioned path to disk. |
| **Hooks** | PreToolUse policy + PostToolUse audit extend governance to Claude Code's **native** tools (Read/Write/Edit/Bash) — including parsing Bash command strings. |
| **Onboarding** | `claude-env onboard <repo>` — one interactive step provisions the repo's isolated policy + RAG/memory namespaces and installs a governance `CLAUDE.md`, engineering skills, and review subagents. |
| **Governance ops** | `report` (auditor evidence + chain proof), `replay` (session forensics), `incident on/off` (kill switch — everything fails closed), `policy-sim` (dry-run + drift). |
| **Self-learning** | Nightly session ingestion turns Claude Code transcripts into memory; a retrieval-feedback loop boosts chunks whose files later get edited. |
| **Observability** | Local latency / retrieval-quality / token-cost metrics with a terminal summary and an optional read-only Datasette UI; per-repo monthly cost budgets. |
| **Dev tools** | `know` (fused memory+RAG+git recall), `context-pack`, `rag-bench`, `test-impact`, `doc-drift`, nightly `digest`. |
| **Packaging** | `claude-plugin/` ships hooks + MCP servers + slash commands as one installable Claude Code plugin. |

---

# Part II — Architecture

## 3. Invariants (the ground rules)

These hold everywhere; the rest of the system is built to preserve them.

- **Persistence is abstracted.** No module imports `sqlite3`/`psycopg` directly —
  everything goes through `lib/db.py::get_db()`. SQLite is the default; PostgreSQL is a
  config change (Appendix B).
- **The audit ledger is the spine.** `audit_events` is append-only and hash-chained; DB
  triggers reject UPDATE/DELETE. Every other audit-like table is a typed projection
  written in the same transaction.
- **Policy is enforced at one chokepoint.** The `filesystem-policy` MCP server is the only
  sanctioned path to the filesystem for agents; nothing downstream re-checks policy, so
  that server is strict and fail-closed.
- **Retrieved/external text is data, never instructions.** RAG results and fetched docs
  are wrapped in `<retrieved_context>` / `<external_doc>` delimiters and screened by the
  poison/injection detectors.
- **No network egress by default.** Models run locally (llama.cpp + ONNX). The only
  outbound capability is the documentation fetch tool, restricted to tier 0/1.
- **Tiers tighten posture monotonically.** 0 public → 1 internal → 2 sensitive → 3
  highly-restricted. Higher tier = more deny rules, memory isolation, and (tier 3)
  default-deny on reads.

## 4. System topology

Everything is local. Claude Code reaches the machine through **two enforcement surfaces** — the
**MCP servers** (stdio, no ports, per-project env) and the **native-tool hooks** (Read/Write/Edit/
Bash). Both consult one **policy engine** and write one **append-only, hash-chained audit ledger**
(the security spine), over local knowledge stores and local inference. The only network egress is
the documentation server for tier-0/1 repos.

![claude-env overall architecture](docs/assets/architecture.svg)

The six MCP servers are `filesystem-policy` (the chokepoint), `git` (read-mostly), `lancedb-rag`,
`memory-graph`, `terminal` (allow-listed), and `documentation` (tier-gated fetch). They start in a
fixed order (`startup_order` in `config/mcp-servers.json`) and import platform code from
`$CLAUDE_ENV_HOME`, so a DB-backend swap is transparent to them.

## 5. Subsystems

File paths refer to this repository; `bootstrap.py` mirrors the code under
`$CLAUDE_ENV_HOME/` at install time.

### 5.1 Foundation — host layout, persistence, audit spine
**Components:** `bootstrap.py`, `lib/db.py`, `sql/001_schema.sql`, `sql/002_retention.sql`,
`sql/003_extensions.sql`, `audit/audit_logger.py`, `validation/validate_installation.py`.

The venv at `$CLAUDE_ENV_HOME/venv/` is the only Python environment used to run platform
code. `bootstrap.py` creates it (preferring `uv`, falling back to stdlib `venv` + pip) and
installs all runtime deps into it. Persistence is `CLAUDE_ENV_DSN` (default
`sqlite:///$HOME/.claude-env/state/claude-env.db`). No secrets are ever stored in config.
Security posture: append-only triggers on `audit_events`; WAL mode; foreign keys on; DB
under `$CLAUDE_ENV_HOME/state` (`chmod 700 $CLAUDE_ENV_HOME` recommended).

### 5.2 Security framework — audit ledger + detectors
**Components:** `audit/audit_logger.py` (typed helpers: `agent_action`, `tool_call`,
`retrieval`, `memory_read/write`, `security_event`, `policy_violation`,
`human_approval_request/resolve`, `verify_chain`); `security/detectors.py`
(`PromptInjectionDetector`, `SecretDetector`, `RagPoisonDetector`).

The chain is tamper-evident: `event_hash = sha256(prev_hash || canonical_json(payload))`.
Canonical-JSON hashing is engine-independent, so PostgreSQL keeps the identical chain.
Detectors are pure functions over text and log every flag as a `security_events` row, so
detection itself is auditable.

### 5.3 Repository guardrails — the policy engine
**Components:** `security/policy_engine.py` (layered evaluation + content scan,
gitignore-style `**` globbing), `config/global-policy.yaml` (never-overridable global deny
+ tier matrix), `config/repo-policy.template.yaml` (annotated per-repo schema).

**Resolution order:** global deny → tier overrides → repo deny → repo allow → fall-through
(tier-3 = default-deny). **Deny always wins.** Allowed by default: source, docs, ADRs,
RFCs, tests. Blocked by default: `.env .pem .p12 .key .crt`, `.ssh`, `.gnupg`, secrets,
credentials, production configs, customer data, exports, backups. The engine is stateless —
edit the YAML and it recompiles on load. Full schema is in [§15](#15-repo-policyyaml-reference).

### 5.4 Local RAG
**Components:** `rag/config.py`, `rag/chunkers/chunkers.py`, `rag/embeddings/llama_embedder.py`,
`rag/rerankers/cross_encoder.py`, `rag/retrievers/lance_store.py`, `rag/indexers/indexer.py`,
`rag/pipelines/retrieve.py`, plus `bootstrap_rag.py`, `repository_scan.py`,
`incremental_index.py`, `branch_index.py`, `validate_rag.py`.

**Model selection lives entirely in config** — the code never names a model. Resolution
order: env vars > `config/rag.yaml` > numeric fallbacks (ctx/dim/gpu only; an unconfigured
`embedding.model_path` raises a clear error rather than guessing). RAG code always goes
through `rag.config.get_embedder()` / `get_reranker()`. Setup is [§10](#10-local-models).

**LanceDB layout:** `$CLAUDE_ENV_HOME/knowledge/lancedb/<slug>__<branch>.lance`, one table
per repo+branch, rows carrying the vector + chunk metadata + tier, with an FTS index for
hybrid search. **Security:** the indexer runs every candidate path through the policy engine
(blocked paths logged), redacts/blocks secrets before embedding, indexes only committed
files when `index_only_committed: true`, and screens retrieved chunks with `RagPoisonDetector`.

**Feedback loop** (`observability/feedback.py`): every returned chunk is recorded
(`signal='retrieved'`); the nightly session ingestor upgrades retrieval→edit correlations to
`signal='used'`, earning a bounded post-rerank boost (`0.05·ln(1+min(uses,20))`) — enough to
re-order near-ties, never to overrule a clear reranker decision. Disable with
`CLAUDE_ENV_FEEDBACK_BOOST=false`.

### 5.5 Memory graph
**Components:** `memory/memory_manager.py`, `memory_retriever.py`, `memory_consolidator.py`,
`memory_pruner.py`, `memory_validator.py`, `session_ingestor.py`, `memory_sync.py`.

A local property graph (nodes + edges in SQLite). Memory types → storage: episodic
(`session/decision/investigation`), semantic (`entity/concept/architecture`), procedural
(`workflow/convention/pattern`, namespace `global`), agent (namespace `agent:<id>`). Edges:
`RELATES_TO`, `DEPENDS_ON`, `DECISION_ABOUT`, `DISCOVERED_IN`, `SUPERSEDES`, `CONSOLIDATES`;
traversal is a recursive CTE (`expand()`). Confidence decays:
`effective = stored · 2^(-age_days / half_life_days)` (sessions 30d, decisions/architecture
365d, conventions 540d, …). **Security:** namespace isolation (`isolated=True` refuses
cross-namespace reads — enforced for tier ≥ 2); corrections via `supersede` (never
destructive overwrite); pruning archives to `archive/memory/<ns>.jsonl` first and never
prunes `decision`/`architecture`.

**Self-population** (nightly): Claude Code transcripts under `~/.claude/projects/` become one
episodic `session` node each (task, files touched, outcome — secret-redacted, deduped). This
also closes the RAG feedback loop.

### 5.6 Multi-agent framework
**Components:** `agents/agent_registry.yaml`, `agents/prompts/*.md` (11 system prompts),
`agents/orchestration/{task_router,agent_handoff,conflict_resolver,approval_gate}.py`.

Each agent's `allowed_tools`, `denied_tools`, `write_paths`, `memory/rag_access`, and
`requires_approval` are declared in the registry. Examples: architect is read-only on source
(memory write); backend writes `src/**`,`tests/**`; database writes migrations/schema and
never touches a live DB; devops `requires_approval: true`; security is read-only; research
does web fetch only in tiers 0–1. **Flow:** decompose → `task_router.route()` picks a
specialist → `approval_gate.evaluate()` → `agent_handoff.handoff()` packages scoped context
as data → specialist acts via MCP → `conflict_resolver.resolve()` reconciles (security veto
wins) → orchestrator synthesizes. Global approval gates: writes outside scope, state-mutating
terminal, any tier-2/3 action, `git push/amend/rebase`, memory delete/prune.

### 5.7 MCP layer
**Components:** `config/mcp-servers.json`, `mcp-servers/*/server.py`.

| Server | Scope | Notes |
|---|---|---|
| `filesystem-policy` | read/write/list | The single FS chokepoint; policy + content-scan; rejects path traversal; fail-closed. |
| `git` | log/diff/blame/status/show | Read-only; denies push/amend/rebase/reset. |
| `lancedb-rag` | search | Read-only, data-wrapped, poison-screened. |
| `memory-graph` | recall/read/expand/write/link | Namespace isolation; no delete (approval-gated). |
| `terminal` | run_tests/benchmarks/audit | Allow-listed only; `terminal.run` returns an approval directive; scrubbed env + timeouts. |
| `documentation` | search/fetch | Local search always; external fetch only in tiers 0–1. |
| `jetbrains` | context/inspect/refactor | Optional, IDE-provided; respects the filesystem policy. |

### 5.8 Claude Code hooks — governing native tools
**Components:** `hooks/policy_hook.py` (PreToolUse), `hooks/audit_hook.py` (PostToolUse),
`hooks/install_hooks.py` (idempotent `~/.claude/settings.json` merge; `claude-env hooks`).

MCP servers only govern MCP traffic — Claude Code's own Read/Write/Edit/Glob/Grep/Bash would
otherwise bypass them. The hooks make the same policy engine govern everything:

**PreToolUse decision flow.** incident marker → deny all → path policy block → **deny**
(+ `policy_violations` row) → secret pattern in Write/Edit content → **ask** → else allow
silently. **Bash is parsed, not skipped** (`shlex` → simple-command segments): a
**destructive/state-mutating command is hard-denied** regardless of path (`rm`, `rmdir`,
`shred`, `dd`, `truncate`, `chmod`/`chown`, `kill`, and `git push`/`reset`/`rebase`/`clean`/
`commit --amend`) — the model must edit via the Write/Edit tools or route the command through
`terminal.run` (approval); a file argument on a policy-denied path is **denied**; a
network-egress command with a file argument is **denied** as likely exfiltration; plain network
egress is **denied** on tier ≥ 2 / **asked** on tier ≤ 1; an inline secret prompts an **ask**.
URLs and non-existent bare tokens aren't treated as local paths (so `grep pattern` and `git log`
aren't misread as files). Note the limits: arbitrary interpreters (`python x.py`, `make`) can't
be statically judged, and the hook fails **open** by default — so this is a strong stop for the
common destructive-shell class, not a full sandbox; keep `CLAUDE_ENV_HOOK_FAIL_CLOSED=true` for tier 2+.

**Failure posture.** Internal errors fail **open** by default so a broken hook can't brick
the editor; `CLAUDE_ENV_HOOK_FAIL_CLOSED=true` flips that for tier-2+ machines. PostToolUse
writes `native.<Tool>` rows for mutating calls (`CLAUDE_ENV_HOOK_AUDIT_ALL=true` audits all).

### 5.9 Governance operations
- **Incident mode** (`security/incident.py`) — a JSON marker at `state/INCIDENT` makes every
  policy evaluation fail closed at once (MCP, indexer, hooks). `on` also denies pending
  approvals, snapshots the SQLite DB, and writes a critical `security_event`.
- **Compliance reports** (`audit/compliance_report.py`) — windowed ledger evidence (events by
  type/actor, top tools, violations, approval trails, session costs) + a fresh `verify_chain()`
  proof. Exit 1 when the chain is broken. Formats: md / csv / json.
- **Session replay** (`audit/session_replay.py`) — orders a session's ledger rows into a
  timeline; forensics without touching the DB.
- **Policy simulation** (`security/policy_sim.py`) — `simulate` reports which files a candidate
  policy would newly block/allow; `diff` is structural drift against a baseline.

### 5.10 Observability
**Components:** `observability/collectors.py`, `feedback.py`, `budgets.py`, `dashboard.py`;
`metrics_sessions` (tokens, est_cost_usd), `metrics_latency`, `metrics_retrieval_quality`.
All local; the cost model is a configurable price table (`PRICES` in `collectors.py`);
Datasette runs read-only on localhost; `metrics_*` are prunable and never contain audit data.

### 5.11 Developer experience tools
All offline, stateless over the working tree + git.
- `rag/context_pack.py` — generates `CLAUDE.generated.md` from repo signals + team memory;
  refuses to overwrite a hand-written `CLAUDE.md`.
- `rag/test_impact.py` — changed files → minimal test set (name + import heuristics).
- `rag/doc_drift.py` — markdown→code references: broken paths, docs older than the code.
- `agents/analysts/nightly_analyst.py` — nightly per-repo digest (doc drift + TODO aging +
  possibly-dead symbols + hotspots) → `logs/digests/` and a memory node.
- `agents/orchestration/approvals_ui.py` — localhost one-click audited approve/deny.

## 6. Threat → control mapping

| Threat | Control |
|---|---|
| Prompt injection | `PromptInjectionDetector` + data-delimited context + "instructions only from operator" rule |
| RAG poisoning | `RagPoisonDetector.scan_chunk` at index and serve time |
| Secret exposure | policy ext/path/regex deny + content scan (read *and* write) + `SecretDetector` + Bash-command scan |
| Memory corruption | `memory_validator` (dangling edges, bad confidence, supersede cycles, ns leakage) `--repair` |
| Policy bypass | single FS chokepoint + path-traversal rejection + fail-closed — extended to native tools by the PreToolUse hook |
| MCP over-permission | explicit scopes + denied tools + allow-listed terminal |
| Active compromise / runaway agent | incident mode: one marker fails every evaluation closed, denies approvals, snapshots the DB |
| Repo isolation | per-repo policy + per-repo LanceDB table + per-repo memory namespace |
| Agent isolation | registry permissions + approval gates + scoped handoffs |

## 7. On-disk layout

`$CLAUDE_ENV_HOME` (default `~/.claude-env/`) after bootstrap:

```text
state/          claude-env.db (audit + memory + metrics + RAG bookkeeping) · INCIDENT marker
venv/           the ONLY Python environment used to run platform code
knowledge/      lancedb/<slug>__<branch>.lance   docs/ (local doc corpus)
models/         downloaded GGUF / ONNX model files (real files, never symlinks)
archive/        memory/  audit/  security/  + DB snapshots
logs/           incremental_index.log · memory.log · digests/
config/         global-policy.yaml · repo-policy.template.yaml · rag.yaml · mcp-servers.json · budgets.yaml
bin/            claude-env CLI · <mirrored platform code: security/ audit/ rag/ memory/ agents/ mcp-servers/ hooks/ templates/ …>
```

Source-tree layout of this repository:

```text
claude-env/
├── bootstrap.py              # one-command setup (uv-preferred venv + deps)
├── requirements.txt          # locked runtime deps
├── bin/claude-env            # CLI dispatcher (auto-resolves the venv Python)
├── lib/db.py                 # persistence abstraction (SQLite default, PG-ready)
├── sql/                      # 001_schema · 002_retention · 003_extensions
├── audit/                    # audit_logger · compliance_report · session_replay
├── hooks/                    # policy_hook (PreToolUse) · audit_hook (PostToolUse) · install_hooks
├── security/                 # policy_engine · policy_sim · incident · detectors
├── config/                   # global-policy · repo-policy.template · rag.yaml · mcp-servers.json · budgets
├── rag/                      # config · chunkers · embeddings · rerankers · retrievers · indexers · pipelines · tools
├── memory/                   # manager · retriever · consolidator · pruner · validator · session_ingestor · sync
├── agents/                   # agent_registry.yaml · prompts/*.md · orchestration/ · analysts/
├── mcp-servers/              # filesystem-policy · git · lancedb-rag · memory-graph · terminal · documentation
├── observability/            # collectors · feedback · budgets · dashboard
├── validation/               # validate_installation · _security · _memory · _agents · _mcp · _features
├── scripts/                  # post-commit · nightly_memory.sh · systemd/ · launchd.README.md
├── templates/repo-onboarding # CLAUDE.md + .claude/{skills,agents} installed into onboarded repos
├── claude-plugin/            # Claude Code plugin packaging (hooks + MCP + slash commands)
└── tests/                    # test_policy_engine · test_policy_hook_bash
```

---

# Part III — Installation

## 8. Prerequisites

| Check | Command | Expected |
|---|---|---|
| macOS Apple Silicon | `uname -m` | `arm64` (Linux also supported) |
| FileVault | — | enabled (encrypts the local store, weights, DB) |
| Xcode CLT | `xcode-select -p` | a path |
| Homebrew | `brew --version` | any version |
| Python ≥ 3.13 | `python3 --version` | `3.13.x`+ (`brew install python@3.13` if older) |
| Git ≥ 2.40 | `git --version` | `2.40`+ |
| Claude Code | `claude --version` | any version |

## 9. Bootstrap

Run once on a new machine, from the repo checkout:

```bash
python3 bootstrap.py --with-brew      # create venv, install deps, init DB + policies, genesis event
chmod 700 ~/.claude-env               # lock down the store

echo 'export CLAUDE_ENV_HOME="$HOME/.claude-env"' >> ~/.zshrc
echo 'export PATH="$CLAUDE_ENV_HOME/bin:$PATH"'   >> ~/.zshrc
source ~/.zshrc

~/.claude-env/venv/bin/python --version   # must print 3.13+
claude-env validate installation          # green except model warnings until §10
```

**What bootstrap does:** checks the host → creates `~/.claude-env/` layout → creates the venv
(via `uv` when available, else stdlib `venv`) → installs all runtime deps (llama-cpp-python is
built with Metal on Apple Silicon) → applies `sql/00*.sql` → copies policy + rag config →
mirrors platform code under `$CLAUDE_ENV_HOME` → writes and verifies the genesis audit event.

**Useful flags:** `--no-deps` (skip pip; venv must exist) · `--no-venv-create` (reuse venv,
update deps) · `--recreate-venv` (nuke + rebuild, e.g. after a Python upgrade) · `--dsn
postgresql://…` (PostgreSQL target).

## 10. Local models

**Required before indexing.** The code ships with **no model** — you download one and point
`config/rag.yaml` at it. The models named below are suggestions; any GGUF embedding model and
any ONNX cross-encoder work. **No symlinks** — point the config straight at the file.

### 10a. Embedding model (required)

```bash
mkdir -p ~/.claude-env/models
~/.claude-env/venv/bin/pip install huggingface-hub    # provides the `hf` CLI
hf download nomic-ai/nomic-embed-text-v1.5-GGUF \
  nomic-embed-text-v1.5.Q8_0.gguf --local-dir ~/.claude-env/models
```

Point the **deployed** config at it — `~/.claude-env/config/rag.yaml` is the copy the servers
actually read:

```yaml
# ~/.claude-env/config/rag.yaml
embedding:
  model_path: "~/.claude-env/models/nomic-embed-text-v1.5.Q8_0.gguf"
  embedding_dim: 768                     # MUST match your model (nomic = 768)
  document_prefix: "search_document: "   # nomic needs task prefixes; leave empty if yours doesn't
  query_prefix:    "search_query: "
```

```bash
claude-env validate installation        # "embedding model file present" must PASS
```

Suggested settings (not requirements): 768-dim GGUF at Q8_0 (Q4/Q5 degrade recall); context
2048; chunk sizing 512 tokens (code) / 384 (markdown) / 256 (sections) with ~12% overlap.

### 10b. Reranker (optional)

```bash
# download an ONNX cross-encoder (the arm64-quantized variant is fastest on Apple Silicon)
hf download cross-encoder/ms-marco-MiniLM-L6-v2 \
  --include "onnx/model.onnx" "onnx/model_qint8_arm64.onnx" \
            "tokenizer.json" "config.json" "vocab.txt" \
            "tokenizer_config.json" "special_tokens_map.json" \
  --local-dir ~/.claude-env/models/reranker-onnx/

# the loader expects model.onnx in the directory root — move the quantized file into place
mv ~/.claude-env/models/reranker-onnx/onnx/model_qint8_arm64.onnx \
   ~/.claude-env/models/reranker-onnx/model.onnx
```

Point the deployed config at the directory, then verify:

```yaml
# ~/.claude-env/config/rag.yaml
reranker:
  model_dir: "~/.claude-env/models/reranker-onnx"   # or "" to disable
```

```bash
claude-env validate installation        # "reranker loads + runs" PASS (WARN = disabled/absent)
```

The reranker degrades gracefully to fusion order if absent or disabled — RAG still works.

### 10c. Choosing / swapping models (no code changes)

Resolution order: **env var > `config/rag.yaml` > numeric fallback.** Relevant env vars:

| Variable | Meaning | Default |
|---|---|---|
| `EMBED_MODEL_PATH` | path to the `.gguf` | **required** (unset ⇒ clear error) |
| `EMBED_MODEL_NAME` | provenance label in `rag_index_state` | filename |
| `EMBED_DOC_PREFIX` / `EMBED_QUERY_PREFIX` | per-task input prefixes | `""` |
| `EMBED_CTX` / `EMBED_GPU_LAYERS` / `EMBED_DIM` | context / GPU offload / vector dim | 2048 / -1 / 768 |
| `RERANKER_DIR` | ONNX cross-encoder dir (`""` disables) | `""` |
| `LANCEDB_PATH` | vector store location | `~/.claude-env/knowledge/lancedb` |

Verify with `claude-env validate installation` — the `embedding model file present` and
`reranker loads + runs` lines must PASS (reranker WARN = disabled/absent, RAG still works). In
Python: `from rag.config import get_reranker; print(get_reranker().status())` — `ok: True`
means reranking is live.

> **Vectors from different models/dimensions are not comparable.** After swapping the
> embedding model, drop the affected LanceDB tables and re-index ([§28](#28-troubleshooting),
> "Removing a repo's RAG index"). Gate swaps with `claude-env rag-bench` (recall@k + MRR).

> **Editing config:** the servers and tools read the **deployed** copy at
> `~/.claude-env/config/rag.yaml` — edit that directly and it takes effect immediately. If you
> instead edit the repo's `config/rag.yaml`, re-run `python3 bootstrap.py --no-deps` to sync
> the deployed copy.

## 11. Native-tool hooks

Extend the policy engine + audit to Claude Code's native tools (Read/Write/Edit/Glob/Grep/Bash):

```bash
claude-env hooks              # install into ~/.claude/settings.json
claude-env hooks --dry-run    # preview

# To uninstall
claude-env hooks --uninstall  # uninstall hooks from  ~/.claude/settings.json
```

Restart Claude Code, then confirm: ask Claude to read a blocked file (e.g. `.env`) — the call
must be denied with the matched rule. On tier-2+ machines set `CLAUDE_ENV_HOOK_FAIL_CLOSED=true`
in the environment Claude Code runs under. Behavior detail is in [§5.8](#58-claude-code-hooks--governing-native-tools).

---

# Part IV — Onboarding a repository

## 12. One-step onboarding

Onboard each repository with a single interactive command:

```bash
claude-env onboard /absolute/path/to/repo        # interactive (recommended)
claude-env onboard /absolute/path/to/repo --yes  # non-interactive; accept detected defaults
```

Interactively it asks a few questions with detected defaults (Enter accepts each):

```text
  Repo slug (used for RAG + memory namespaces) [my-repo]:
  Privacy tier — 0 public / 1 internal / 2 sensitive / 3 restricted [1]: 2
  Default branch [main]:
  Short description (optional) []: billing service
```

It then provisions the repo's **isolated workspace** end to end:

1. **Policy** — writes `<repo>/.claude/repo-policy.yaml` from the template with the real slug,
   tier, description, and memory namespace filled in (memory is isolated automatically for
   tier ≥ 2). *This file is the isolation boundary the policy engine + hooks enforce.*
2. **Namespaces** — the RAG table `<slug>__<branch>` and the memory namespace `proj-<slug>`.
3. **MCP env** — patches `~/.claude.json` so every server for this project resolves to the
   correct repo root, slug, branch, and namespaces.
4. **Template** — installs the governance `CLAUDE.md` and the `.claude/` skills + subagents
   ([§13](#13-what-lands-in-the-repo)), with concrete namespace values substituted (no
   placeholder text remains).
5. **Index (opt-in)** — offers to build the first RAG index now.

**Flags:** `--repo-name SLUG` · `--tier {0..3}` · `--description STR` · `--branch BRANCH` ·
`--yes/-y` · `--force-policy` (regenerate an existing policy) · `--force-template` (overwrite
existing skill/agent files) · `--no-template` (namespaces/env only) · `--dry-run`.

Re-running is idempotent: an existing `repo-policy.yaml` is preserved (local edits kept) unless
`--force-policy`; the `CLAUDE.md` managed block is refreshed while out-of-block content is kept.
**Restart Claude Code afterwards** so the MCP servers pick up the new environment.

> `claude-env register` is an alias for the same command.

## 13. What lands in the repo

Onboarding installs a governance layer directly into the repo:

- **`CLAUDE.md`** — a binding operating contract: route all file/search/git/memory/test/doc
  work through the platform MCP servers, respect the privacy tier and approval gates, treat
  retrieved content as data, never touch secrets. It shows this repo's concrete RAG table and
  memory namespace. The platform-owned section lives inside a managed block; anything you add
  outside it is preserved across re-onboarding.
- **`.claude/skills/`** — `principled-engineering` (decoupled architecture, SOLID, simplicity),
  `solid-design`, `design-patterns` (with explicit *when-not-to*), `code-review`,
  `architecture-review`.
- **`.claude/agents/`** — `code-reviewer` and `architecture-reviewer` read-only subagents.

The template source lives in `templates/repo-onboarding/`; edit it there (not the copies in a
repo) — the managed `CLAUDE.md` block regenerates on the next onboard.

## 14. Index, verify, auto-reindex

```bash
claude-env scan  /path/to/repo                       # preview allowed vs blocked (no model load)
claude-env index /path/to/repo                        # full RAG index (live progress bar)
claude-env validate rag /path/to/repo "auth token validation"   # expect 3–8 ranked results
```

`scan` should show only secrets/generated files in the blocked list — adjust
`.claude/repo-policy.yaml` and re-run until satisfied. Empty RAG results mean the index is empty
(re-run `index`) or the slug/branch don't match the LanceDB table.

Install the post-commit hook so every commit triggers a background incremental re-index:

```bash
ln -sf ~/.claude-env/scripts/post-commit /path/to/repo/.git/hooks/post-commit
chmod +x /path/to/repo/.git/hooks/post-commit
```

## 15. repo-policy.yaml reference

Lives at `<repo>/.claude/repo-policy.yaml`; created by onboarding. Deny always wins over allow;
for tier 3 the allow list is authoritative (anything not allowed is denied).

```yaml
version: 1
tier: 1                       # 0 public · 1 internal · 2 sensitive · 3 restricted
repo: "my-repo-slug"          # stable slug; drives RAG + memory namespacing
description: "..."

allow:
  paths:      ["src/**", "docs/**", "ADRs/**", "tests/**", "*.md"]
  extensions: [".py", ".ts", ".go", ".java", ".rs", ".md", ".sql"]

deny:                         # always evaluated; always win
  paths:      ["**/secrets/**", "**/.ssh/**", "**/config/prod.*", "**/customer_data/**"]
  extensions: [".env", ".pem", ".key", ".crt", ".keystore"]
  regex:      [{pattern: '(^|/)\.env($|\.)', reason: "dotenv files"}]

content_scan:                 # applied to allowed files before bytes leave the engine
  enabled: true
  on_match: "redact"          # redact | block
  patterns: [{name: "aws_access_key", pattern: 'AKIA[0-9A-Z]{16}'}]

rag:
  enabled: true               # tier 3 forces false unless opted in
  index_paths:   ["src/**", "docs/**", "README.md"]
  exclude_paths: ["**/node_modules/**", "**/dist/**", "src/generated/**"]
  index_only_committed: true  # never index the working tree / untracked files

memory:
  namespace: "proj-my-repo-slug"
  isolated: false             # tier ≥ 2 forces true (no cross-project reads)
  share_with_agents: [architect, backend, testing, documentation]

agent_permissions:            # per-agent override of registry defaults for THIS repo
  devops: { requires_approval: true }
```

Preview a candidate policy before applying it: `claude-env policy-sim simulate /repo
--candidate new-policy.yaml`.

## 16. Register MCP servers (optional)

You normally **don't need this.** `claude-env onboard <repo>` ([§12](#12-one-step-onboarding))
already writes each project's full MCP server configuration — command, args, **and** the per-repo
env — straight into `~/.claude.json`, so registration happens per repository as part of onboarding.

Do this **only** to make the servers available in every project (including repos you haven't
onboarded yet), by registering them once at **user scope**. It matters that `claude mcp add`
defaults to `local` scope — the *current project only* — which is why running it inside a directory
registers the server to just that project. `--scope user` stores the definitions in `~/.claude.json`
for all projects, and the working directory is irrelevant, so run it from anywhere:

```bash
# --scope user = machine-wide; run from any directory. Use the venv Python explicitly —
# Claude Code launches servers outside any shell, so bare `python` would not resolve to the venv.
H=~/.claude-env; PY="$H/venv/bin/python"
claude mcp add filesystem-policy --scope user -- "$PY" "$H/mcp-servers/filesystem-policy/server.py"
claude mcp add git               --scope user -- "$PY" "$H/mcp-servers/git/server.py"
claude mcp add lancedb-rag       --scope user -- "$PY" "$H/mcp-servers/lancedb-rag/server.py"
claude mcp add memory-graph      --scope user -- "$PY" "$H/mcp-servers/memory-graph/server.py"
claude mcp add terminal          --scope user -- "$PY" "$H/mcp-servers/terminal/server.py"
claude mcp add documentation     --scope user -- "$PY" "$H/mcp-servers/documentation/server.py"
claude mcp list                                   # all six appear
```

These user-scope entries start with an empty `env`, so they don't yet know which repo they serve —
`claude-env onboard <repo>` still supplies each project's `CLAUDE_ENV_REPO_ROOT`, slug, branch, and
namespaces. Scope precedence is **local (project) > user**, and Claude Code uses one whole entry
rather than merging them, so an onboarded repo always uses its own project-scoped entry (correct
env); the user-scope entry is only the fallback for not-yet-onboarded projects.

---

# Part V — Operations

## 17. Everyday verification

After restarting Claude Code, confirm each server by asking Claude to use it:

| Server | Ask | Expected |
|---|---|---|
| `git` | "what files are modified?" | lists modified files |
| `filesystem-policy` | "list files in src/" | file listing |
| `lancedb-rag` | "search the codebase for X" | ranked chunks |
| `memory-graph` | "what do you remember?" | memory nodes (may be empty initially) |
| `terminal` | "run the tests" | runs configured tests or a gated directive |
| `documentation` | "search docs for X" | local doc matches |

`fatal: not a git repository` or empty RAG results almost always mean missing/wrong `env` in
`~/.claude.json` — re-run `claude-env onboard` and restart.

## 18. Memory maintenance

```bash
PY=~/.claude-env/venv/bin/python
$PY memory/memory_validator.py    --all --repair                 # integrity + safe repair
$PY memory/memory_consolidator.py --all                          # merge low-value clusters
$PY memory/memory_pruner.py       --all                          # dry-run report
$PY memory/memory_pruner.py       --namespace proj-x --apply     # actually prune (archives first)
```

Pruning archives to `~/.claude-env/archive/memory/<ns>.jsonl` and never prunes `decision` /
`architecture` nodes.

## 19. Approvals

Governance has **two enforcement layers**, and it's worth knowing which one you're hitting:

- **`terminal.run` opens a human approval and blocks on it.** Asking Claude to run a free-form /
  state-mutating command opens a pending approval in `human_approvals`, **auto-opens the approvals
  web UI in your browser**, and then **waits** — the tool call blocks (Claude waits, up to
  `CLAUDE_ENV_APPROVAL_WAIT_S`, default 120s) until you decide. You review it (project, session,
  agent, tier, exact command) and Approve/Deny in the UI; the decision records the **OS user@host**
  automatically (no name asked). **On approve the command then runs** (same sandbox: argv-only, no
  shell/pipes, scrubbed env, repo-root cwd, timeout) and Claude gets the output; on deny/timeout it
  doesn't run. Auto-UI is toggled with `CLAUDE_ENV_APPROVAL_AUTO_UI` (default on).
- **Hard-deny (no gate)**: `git push`/`amend`/`rebase`/`reset --hard` at the git server, and the
  native-tool **hooks** ([§11](#11-native-tool-hooks)) — these simply refuse (deny / `ask`-to-
  confirm secret writes) rather than opening an approval.
- **The orchestration approval gate** (`agents/orchestration/approval_gate.py`) also *opens*
  approvals when you drive work through the agent orchestrator — `claude-env route "<task>"` — or
  on any tier-2+ action.

Open the queue with:

```bash
claude-env approvals-ui           # pretty web UI (Approve/Deny)
claude-env approvals --list-open  # terminal list
claude-env services               # which local UI is on which port (auto-assigned if 8002 was busy)
```

> **Ports auto-fall-back.** The approvals UI (preferred 8002) and the dashboard (preferred 8001)
> bind their preferred port if free, else a random free one — so they never collide with your app.
> `claude-env services` shows the actual URL of each running UI (`--port N` pins one, `--port 0`
> forces random).

**A gate opens when ANY of these is true** (evaluated per agent action by the orchestrator):

| Trigger | Example |
|---|---|
| the agent's registry entry has `requires_approval: true` | the `devops` agent (any action) |
| the repo is **tier 2 or 3** | *any* action in a sensitive repo |
| a write targets a path outside the agent's `write_paths` | `backend` writing `infra/main.tf` |
| a state-mutating terminal command | `terminal.run …` (tests/benches/audits run unattended) |
| git history rewrite / push | `git push`, `commit --amend`, `rebase`, `reset --hard` |
| destructive memory op | `memory.delete`, `memory.prune --apply` |
| the action matches the agent's `denied_tools` | hard stop, surfaced as a gate |

Resolve pending gates:

```bash
claude-env approvals --list-open
claude-env approvals-ui --port 8002 --by you        # one-click localhost UI (decisions audited)
~/.claude-env/venv/bin/python agents/orchestration/approval_gate.py --resolve <id> --approve --by you
```

Open approvals also appear in the dashboard and the `v_open_approvals` view; on macOS a
notification fires when a gate opens.

## 20. Observability & budgets

```bash
claude-env dashboard --window 7d
claude-env dashboard serve --port 8001              # Datasette (read-only, localhost)
claude-env budget                                    # per-repo monthly USD; exit 1 if exceeded
claude-env budget --format json
```

Set limits in `~/.claude-env/config/budgets.yaml`. Update `PRICES` in
`observability/collectors.py` to current per-1M-token pricing for accurate cost tracking.

## 21. Governance: compliance, replay, incident, policy-sim

```bash
claude-env report  --window 30d                                  # markdown evidence + chain proof
claude-env report  --window 7d --repo payments --format csv --out evidence.csv
claude-env replay  --list                                        # recent sessions
claude-env replay  <session_id>                                  # step-by-step timeline

claude-env incident on  --reason "suspected token leak" --by you # kill switch (fails everything closed)
claude-env incident status
claude-env incident off --by you

claude-env policy-sim simulate /repo --candidate new-policy.yaml # which files change block/allow
claude-env policy-sim diff /repo/.claude/repo-policy.yaml ~/.claude-env/config/repo-policy.template.yaml
```

A report's integrity line is the first thing to check — a non-`VERIFIED` chain exits 1.

## 22. Knowledge & quality tools

```bash
claude-env know "jwt validation" --repo payments --repo-root /work/payments   # fused memory+RAG+git
claude-env context-pack /work/payments --write                                # CLAUDE.generated.md
claude-env rag-bench    /work/payments --create-template                      # then edit + run
claude-env test-impact  /work/payments --since HEAD~1
claude-env doc-drift    /work/payments
claude-env digest       /work/payments                                        # analyst digest now
```

For nightly per-repo digests, list repo paths (one per line) in
`~/.claude-env/config/analyst-repos.txt`.

## 23. Team knowledge sync

```bash
claude-env memory-sync export --namespace proj-payments --out team.jsonl   # secret-redacted
# review the JSONL, share it, then on the teammate's machine:
claude-env memory-sync import --in team.jsonl                              # additive; --namespace remaps
```

Imports are additive (existing node ids skipped). Review before sharing regardless.

## 24. Backup & recovery

```bash
# Backup (schedule daily)
H=~/.claude-env
sqlite3 "$H/state/claude-env.db" ".backup '$H/archive/db-$(date +%F).sqlite'"   # online, WAL-safe
rsync -a "$H/knowledge/lancedb/" "$H/archive/lancedb-$(date +%F)/"
# The venv does NOT need backup — rebuild with: python3 bootstrap.py --no-venv-create

# Recovery
cp $H/archive/db-YYYY-MM-DD.sqlite $H/state/claude-env.db
rsync -a --delete $H/archive/lancedb-YYYY-MM-DD/ $H/knowledge/lancedb/
$H/venv/bin/python validation/validate_installation.py                        # must exit 0
```

Recovery is complete only when `validate_installation.py` exits 0 and `verify_chain()` returns
`True`.

## 25. Upgrades

```bash
# Schema: add sql/00N_*.sql (bump schema_version), then:
~/.claude-env/venv/bin/python -c "import sys; sys.path.insert(0,'.'); from lib.db import get_db; get_db().apply_schema('sql/00N_whatever.sql')"

# Code + deps:
git pull
python3 bootstrap.py --no-venv-create      # update deps in the existing venv
python3 bootstrap.py --recreate-venv        # rebuild venv from scratch (after a Python upgrade)
```

Always snapshot the DB ([§24](#24-backup--recovery)) before upgrading; restore on failure.

## 26. Nightly automation

The nightly job ingests session transcripts, runs memory maintenance, and writes per-repo
digests.

- **macOS (launchd):** follow `scripts/launchd.README.md`.
- **Linux (systemd):**
  ```bash
  mkdir -p ~/.config/systemd/user
  cp ~/.claude-env/scripts/systemd/claude-env-nightly.{service,timer} ~/.config/systemd/user/
  systemctl --user daemon-reload
  systemctl --user enable --now claude-env-nightly.timer
  ```

Run any piece manually: `claude-env ingest-sessions` (parse new transcripts) ·
`claude-env digest <repo>`.

---

# Part VI — Reference

## 27. CLI command reference

`claude-env <command>` dispatches through the venv Python automatically. Run `claude-env`
(or `claude-env help`) for a grouped overview, `claude-env help <command>` for a one-line
summary, and `claude-env <command> --help` for a command's full options.

| Command | Purpose |
|---|---|
| `bootstrap` | run `bootstrap.py` |
| `onboard` / `register` | interactive repo onboarding (policy + namespaces + env + template) |
| `scan` | preview policy allow/block split for a repo |
| `index` / `reindex` | full / incremental RAG index |
| `validate [all\|installation\|security\|rag\|memory\|agents\|mcp\|features]` | run validators |
| `hooks` | install/preview/uninstall native-tool hooks |
| `know` | fused memory + RAG + git recall with provenance |
| `context-pack` | generate `CLAUDE.generated.md` from repo signals + memory |
| `rag-bench` / `test-impact` / `doc-drift` / `digest` | quality tools |
| `route` | route a task to a specialist agent |
| `approvals` / `approvals-ui` | list / resolve approval gates |
| `services` | list the live local UI servers and their (possibly auto-assigned) ports |
| `report` / `replay` | compliance evidence / session forensics |
| `incident` | kill switch (on/off/status) |
| `policy-sim` | candidate-policy dry run / drift diff |
| `dashboard` | metrics summary / Datasette UI |
| `budget` | per-repo monthly cost budgets |
| `ingest-sessions` / `memory-sync` | transcript ingest / namespace export-import |

## 28. Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| `validate_installation` fails on venv | bootstrap not run | `python3 bootstrap.py` |
| MCP server won't start | venv Python not in command | ensure `claude mcp add` used `$H/venv/bin/python`, not bare `python` |
| `git` MCP: `fatal: not a git repository` | `CLAUDE_ENV_REPO_ROOT` unset | `claude-env onboard <repo>`; restart Claude Code |
| `filesystem-policy`: "file not found" | wrong/unset repo root | same as above |
| `lancedb-rag` returns empty | wrong `CLAUDE_ENV_REPO_NAME`/`BRANCH` | check `ls ~/.claude-env/knowledge/lancedb/`; re-onboard; restart |
| `memory.recall` always `[]` | nodes have `embedding=NULL` | re-write nodes after model config, or back-fill (below) |
| RAG quality dropped / dim mismatch | embedding model dimension changed | vectors aren't comparable — drop tables + re-index (below) |
| Agent "BLOCKED" on a normal file | over-broad deny rule | inspect `policy_violations`; adjust repo `deny`/`allow` (never global) |
| Reranker not improving results | ONNX absent/failed/disabled | `python -c "from rag.config import get_reranker; print(get_reranker().status())"` — `ok` should be `True` |
| Audit chain reports broken | manual edit / partial restore | restore from a known-good backup; never edit `audit_events` |
| Memory leaks across repos | `CLAUDE_ENV_MEMORY_ISOLATED` unset for tier ≥ 2 | set tier ≥ 2 in repo policy and re-onboard |
| Terminal command "GATED" | state-mutating command | route via `approval_gate.py`; only test/bench/audit run unattended |
| Every native tool call denied | incident mode active | `claude-env incident status`; `claude-env incident off --by you` |
| Bash command denied unexpectedly | it touches a denied path / network egress | see [§5.8](#58-claude-code-hooks--governing-native-tools); use an allowed path or request approval |
| Hooks not firing | settings not installed / stale session | `claude-env hooks` then restart Claude Code |

**Removing a repo's RAG index** (needed after an embedding-model swap):

```bash
rm -rf ~/.claude-env/knowledge/lancedb/${REPO_SLUG}__${BRANCH}.lance/
~/.claude-env/venv/bin/python - <<'PY'
import os, sys; sys.path.insert(0, os.path.expanduser("~/.claude-env"))
from lib.db import get_db
db = get_db(); repo, branch = "your-repo-slug", "main"
db.execute("DELETE FROM rag_file_state  WHERE repo=? AND branch=?", (repo, branch))
db.execute("DELETE FROM rag_index_state WHERE repo=? AND branch=?", (repo, branch))
try: db.commit()
except AttributeError: pass
print(f"Purged RAG index for {repo}@{branch}")
PY
```

**Back-filling memory embeddings** (only if nodes were written with `embedding=NULL`): iterate
`memory_nodes WHERE embedding IS NULL`, embed `name + body_json` with
`rag.config.get_embedder()`, and `UPDATE … SET embedding=?`.

Logs live in `~/.claude-env/logs/` (`incremental_index.log`, `memory.log`, `digests/`).

**Verify the audit chain any time:**

```bash
~/.claude-env/venv/bin/python -c "import sys; sys.path.insert(0,'$HOME/.claude-env'); \
from audit.audit_logger import AuditLogger; print(AuditLogger('ops',actor='ops').verify_chain())"
# (True, None) on a clean ledger
```

## Appendix A — JetBrains integration

Target IDEs: IntelliJ IDEA Ultimate, PyCharm Professional, WebStorm, GoLand, Rider, CLion.

**Setup:** install the **Claude Code** plugin from the JetBrains Marketplace (it bundles the MCP
bridge on recent builds) → `Settings → Tools → Claude Code`: point it at your `claude` CLI,
enable "Use project MCP configuration", and set the project env (`CLAUDE_ENV_REPO_ROOT=$ProjectFileDir$`,
tier, `CLAUDE_ENV_MEMORY_NS=proj-<slug>`). Do **not** install third-party "AI" plugins that
phone home — this platform is local-only.

**Recommended IDE settings:** Actions-on-Save → Reformat + Optimize imports (so agent diffs
match house style); keep language inspections strict (the review agents consume them via the
bridge); enable "Run Git hooks" on commit (so the post-commit indexer fires); mark
`generated/`, `vendor/`, `node_modules/` as Excluded.

**Workflows:** *Code review* — open the diff, ask Claude to review; the orchestrator routes to
Security + Performance (read-only) + Testing; findings are file:line clickable and security ones
land in `security_events`. *Refactor* — the Backend/Frontend agent proposes a diff within its
`write_paths`; prefer IDE-native Rename/Extract for mechanical steps; run tests before accepting.
*Architecture review* — ask the Architect for a module review; output is an ADR/RFC, not code.
*Debug* — give the failure context; agents localize via `git.blame`/RAG, propose a fix + a
regression test, and store the investigation as a memory node.

**Security:** the IDE bridge respects the same `filesystem-policy` chokepoint — a file blocked by
the repo policy is not surfaced even if open in the editor. No separate IDE credential store; no
outbound calls beyond the tier-gated documentation fetch.

## Appendix B — PostgreSQL migration

SQLite is the default and is enough for a single machine. Migrate to PostgreSQL for
multi-machine sharing, concurrent writers, or centralized audit retention. Because all DB access
goes through `lib/db.py`, application code does not change.

**Already portable:** `?` placeholders (rewritten to `%s` for psycopg), the `WITH RECURSIVE`
memory traversal, `ON CONFLICT … DO UPDATE` upserts, and `struct` float32 embedding BLOBs (→ `BYTEA`).

**Dialect deltas** (in a new `sql/00N_pg.sql`): `INTEGER PRIMARY KEY AUTOINCREMENT` →
`BIGSERIAL`; drop SQLite `PRAGMA`s (use server config); `TEXT` timestamps → `TIMESTAMPTZ`
(or keep ISO text for byte-identical audit payloads); `BLOB` → `BYTEA`; re-express the
append-only `BEFORE UPDATE/DELETE` triggers as PL/pgSQL raising an exception; views port unchanged.

**Cutover (offline):** `createdb claude_env` → apply the PG schema via `get_db().apply_schema(...)`
→ **copy `audit_events` first, in ascending `event_id` order** (preserve `event_id`, `prev_hash`,
`event_hash` verbatim — never recompute), then projections, memory, metrics → run `verify_chain()`
on PostgreSQL (must print `(True, None)`) → `setval(...)` the identity sequences → set
`CLAUDE_ENV_DSN=$PGDSN` in shell/launchd/MCP env → re-run the validators. LanceDB is unaffected
(replicate the dir for multi-machine). **Rollback:** the SQLite file is read-only during
migration — point `CLAUDE_ENV_DSN` back at it and re-validate.

**Concurrency:** the audit writer takes a process lock for monotonic chaining; with multiple
machines writing one PostgreSQL ledger, wrap read-prev-hash + insert in a `SERIALIZABLE`
transaction (retry on failure) or funnel audit writes through a single writer service. Memory
and metrics tolerate concurrent writers as-is.

## Appendix C — Plugin distribution

`claude-plugin/` packages the governance layer for a team: the policy + audit hooks
(`hooks/hooks.json`), the core MCP servers (`.mcp.json`), and slash commands
(`/claude-env:know`, `/claude-env:report`, `/claude-env:replay`).

```text
/plugin marketplace add <org>/claude-env
/plugin install claude-env
# or, for local testing:
claude --plugin-dir /path/to/claude-env/claude-plugin
```

The platform must still be bootstrapped ([§9](#9-bootstrap)) and models configured
([§10](#10-local-models)) on each machine — the plugin is only the wiring/distribution layer.
See `claude-plugin/README.md`. Without the plugin system, `claude-env hooks` ([§11](#11-native-tool-hooks)) +
[§16](#16-register-mcp-servers-optional) achieve the same wiring.

## License & use

Internal engineering starter. Review `config/global-policy.yaml` and the tier matrix against
your organization's data-handling requirements before onboarding sensitive repositories.
