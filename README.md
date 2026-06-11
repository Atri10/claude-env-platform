# claude-env

A **privacy-first, local-only AI development platform** for macOS Apple Silicon,
centered on Claude Code. Everything runs on the machine: local embeddings and
reranking (llama.cpp + ONNX), a local LanceDB vector store, a local SQLite memory
graph, and a tamper-evident local audit ledger. No code, embeddings, or telemetry
leave the host.

This repository is a **production-grade starter codebase**, not a sketch. The five
offline-testable validators pass; the policy engine, audit chain, memory graph, and
agent orchestration are implemented and exercised.

## What it gives you
- **Repository guardrails** — a layered policy engine (global deny → tier → repo deny
  → repo allow, deny wins, tier-3 default-deny) plus content secret scanning. Source,
  docs, ADRs, RFCs, tests allowed by default; secrets/keys/prod-configs/PII blocked.
- **Tamper-evident auditing** — append-only, hash-chained `audit_events` with typed
  projections; DB triggers reject any mutation; `verify_chain()` proves integrity.
- **Local RAG** — AST-aware chunking, nomic-embed-text (Q8_0) embeddings, LanceDB
  hybrid search, ONNX cross-encoder rerank, per-repo/branch tables, incremental
  re-index on commit.
- **Memory graph** — episodic/semantic/procedural/agent memory as nodes+edges in
  SQLite, confidence decay, supersede-based corrections, consolidation, pruning,
  namespace isolation.
- **10 specialist agents + orchestrator** — explicit per-agent tools, write scopes,
  memory/RAG scope, and approval gates; deterministic routing, scoped handoffs, and a
  conflict resolver with security veto.
- **MCP layer** — six local stdio servers; the policy-enforcing filesystem server is
  the single sanctioned path to disk.
- **Security hardening** — prompt-injection, RAG-poison, secret, and memory-corruption
  detectors, all auditable.
- **Observability** — local latency / retrieval-quality / token-cost metrics with a
  terminal summary and an optional read-only Datasette UI.
- **PostgreSQL-ready** — all DB access goes through one abstraction; migrating is a
  config change plus a dialect file (see `docs/POSTGRES_MIGRATION.md`).

## Quick start

> **Full step-by-step instructions:** `docs/RUNBOOK.md` — covers every command
> with expected output. `docs/DEPLOYMENT_CHECKLIST.md` — tick-box checklist
> from clean machine to all-green.

```bash
# 1. Bootstrap — creates ~/.claude-env/venv/, installs all deps inside it
python3 bootstrap.py --with-brew
chmod 700 ~/.claude-env

# 2. Add to ~/.zshrc (then open a new terminal or source it)
echo 'export CLAUDE_ENV_HOME="$HOME/.claude-env"' >> ~/.zshrc
echo 'export PATH="$CLAUDE_ENV_HOME/bin:$PATH"'   >> ~/.zshrc
source ~/.zshrc

# 3. Confirm the venv Python, then validate
~/.claude-env/venv/bin/python --version        # expect 3.13+
claude-env validate installation               # model warnings OK until step 4

# 4. Download local models (requires internet once; see RUNBOOK §2)
#    - Embedding: nomic-embed-text-v1.5.Q8_0.gguf → ~/.claude-env/models/
#    - Reranker:  ms-marco-MiniLM-L-6-v2 ONNX     → ~/.claude-env/models/reranker-onnx/
claude-env validate installation               # llama_cpp + onnxruntime must now PASS

# 5. Register MCP servers with Claude Code (see RUNBOOK §3)
H=~/.claude-env; PY="$H/venv/bin/python"
claude mcp add filesystem-policy -- "$PY" "$H/mcp-servers/filesystem-policy/server.py"
claude mcp add git               -- "$PY" "$H/mcp-servers/git/server.py"
claude mcp add lancedb-rag       -- "$PY" "$H/mcp-servers/lancedb-rag/server.py"
claude mcp add memory-graph      -- "$PY" "$H/mcp-servers/memory-graph/server.py"
claude mcp add terminal          -- "$PY" "$H/mcp-servers/terminal/server.py"
claude mcp add documentation     -- "$PY" "$H/mcp-servers/documentation/server.py"

# IMPORTANT: the servers need per-repo env vars to know where your code lives.
# Auto-configure them with one command (then restart Claude Code):
#   claude-env register /abs/path/to/repo
# Use --dry-run to preview first. See RUNBOOK §3b for details.

# 6. Onboard a repository (see RUNBOOK §4)
claude-env scan  /path/to/repo                # preview policy allow/block split
claude-env index /path/to/repo                # full RAG index (progress bar shown)
claude-env validate rag /path/to/repo "test query"
```

From this point, every platform operation goes through the venv Python automatically —
either via `claude-env <cmd>` (the CLI) or the MCP servers (registered with the
`$CLAUDE_ENV_HOME/venv/bin/python` interpreter). You never need to activate the venv
manually.

## Documentation
- `docs/IMPLEMENTATION.md` — the phase-by-phase build blueprint (Phases 0–10) with
  objectives, deliverables, validation, rollback, and acceptance criteria.
- `docs/RUNBOOK.md` — day-2 ops: model prep, MCP registration, backup/recovery, upgrades.
- `docs/DEPLOYMENT_CHECKLIST.md` — clean machine → all-green.
- `docs/JETBRAINS.md` — IDE plugins, settings, and review/refactor/architecture/debug
  workflows.
- `docs/POSTGRES_MIGRATION.md` — the SQLite→PostgreSQL cutover.

## Directory tree
```
claude-env/
├── bootstrap.py                  # one-command setup — creates the venv, installs all deps
├── requirements.txt              # what bootstrap installs into the venv
├── bin/claude-env                # CLI dispatcher (auto-resolves the venv Python)
├── lib/db.py                     # persistence abstraction (SQLite default, PG-ready)
├── sql/
│   ├── 001_schema.sql            # audit + observability + memory + RAG bookkeeping
│   └── 002_retention.sql         # retention/archival policy + views
├── audit/audit_logger.py         # append-only, hash-chained ledger
├── security/
│   ├── policy_engine.py          # layered allow/deny + content scan
│   └── detectors.py              # injection / secret / RAG-poison detectors
├── config/
│   ├── global-policy.yaml        # never-overridable global deny + tier matrix
│   ├── repo-policy.template.yaml # annotated per-repo schema
│   └── mcp-servers.json          # MCP topology: startup order, scopes, venv Python path
├── rag/
│   ├── embeddings/llama_embedder.py
│   ├── chunkers/chunkers.py      # AST-aware + markdown/section chunking
│   ├── retrievers/lance_store.py # per-repo/branch LanceDB tables, hybrid search
│   ├── rerankers/cross_encoder.py
│   ├── indexers/indexer.py       # policy-enforced indexing
│   ├── pipelines/retrieve.py     # embed → search → rerank → data-wrapped context
│   ├── bootstrap_rag.py  repository_scan.py  incremental_index.py
│   ├── branch_index.py   validate_rag.py
├── memory/
│   ├── memory_manager.py  memory_retriever.py   (CRUD + graph CTE recall)
│   ├── memory_consolidator.py  memory_pruner.py  memory_validator.py
├── agents/
│   ├── agent_registry.yaml       # 10 specialists + orchestrator, full permissions
│   ├── prompts/*.md              # 11 system prompts
│   └── orchestration/
│       ├── task_router.py  agent_handoff.py
│       ├── conflict_resolver.py  approval_gate.py
├── mcp-servers/                  # all servers invoked with $CLAUDE_ENV_HOME/venv/bin/python
│   ├── filesystem-policy/server.py   # the policy chokepoint
│   ├── git/server.py                 # read-mostly git
│   ├── lancedb-rag/server.py         # read-only retrieval
│   ├── memory-graph/server.py        # namespaced memory
│   ├── terminal/server.py            # allow-listed commands only
│   └── documentation/server.py       # local search + tier-gated fetch
├── observability/
│   ├── collectors.py             # latency / quality / token-cost writers
│   └── dashboard.py              # terminal summary + datasette launcher
├── validation/
│   ├── validate_installation.py  # checks venv exists + all tables + audit chain
│   ├── validate_security.py  validate_memory.py  validate_agents.py  validate_mcp.py
├── scripts/
│   ├── post-commit               # incremental re-index hook (uses venv Python)
│   ├── nightly_memory.sh         # decay/consolidate/validate/prune (uses venv Python)
│   ├── launchd.README.md         .claudeignore.template
├── tests/test_policy_engine.py
└── docs/
    ├── IMPLEMENTATION.md  RUNBOOK.md  DEPLOYMENT_CHECKLIST.md
    ├── JETBRAINS.md       POSTGRES_MIGRATION.md
```

The venv lives at `$CLAUDE_ENV_HOME/venv/` and is the **only** Python environment
used to run platform scripts. The system Python (used to run `bootstrap.py` once) is
never polluted. `bootstrap.py` creates and populates the venv, and all MCP server
registrations, the CLI, the git hook, and the nightly launchd script all reference
`$CLAUDE_ENV_HOME/venv/bin/python` explicitly.

## Status of the code
Implemented and verified offline (no model downloads needed): policy engine + glob
semantics, audit hash-chain + append-only triggers, memory graph (recall, expand,
supersede, isolation, decay), agent registry + router + gate + conflict resolver, MCP
topology, observability. The RAG embed/rerank path and the MCP servers require their
runtime deps (`llama-cpp-python`, `lancedb`, `onnxruntime`, `mcp`) installed by
`bootstrap.py`; they are written to run on a bootstrapped Apple Silicon host.

## License / use
Internal engineering starter. Review the global policy and tier matrix against your
organization's data-handling requirements before onboarding sensitive repositories.
