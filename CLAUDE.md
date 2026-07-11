# CLAUDE.md — working on claude-env itself

This is the **claude-env platform** repo: a privacy-first, local-only governance layer for
Claude Code (policy-enforced file access, tamper-evident audit ledger, local RAG + memory MCP
servers, approvals, incident mode). This file is guidance for developing *the platform*; the
full product guide is [`README.md`](README.md) (read it once).

> Not to be confused with `templates/repo-onboarding/` — that `CLAUDE.md` + `.claude/` is the
> **deliverable installed into other repos** by `claude-env onboard`. This file governs work on
> the platform's own source.

## Golden workflow rules (do these — they've bitten us)

1. **Branch first — never commit to `master`.** PRs get merged between turns, which leaves you
   back on `master`. Switch to a feature branch as its **own step** and confirm it, *then* stage
   and commit — do **not** chain `git branch --show-current` with `git commit`:
   ```bash
   git checkout -b fix/thing        # separate step; confirm output
   # ...edit...
   git add <files> && git commit    # only after you're on the branch
   ```
   Commit messages end with the Co-Authored-By trailer. Push/PR only when asked.
2. **Deploy edits to `$CLAUDE_ENV_HOME` or they don't take effect.** The live MCP servers, hooks,
   and CLI run from `~/.claude-env/` (a mirror created by `bootstrap.py`), **not** from this repo.
   After editing platform code, copy it over (or re-mirror) and restart Claude Code for MCP/hook
   changes:
   ```bash
   cp -v mcp-servers/terminal/server.py ~/.claude-env/mcp-servers/terminal/server.py   # targeted
   python3 bootstrap.py --no-deps                                                       # re-mirror code
   ```
   Config files (`config/*.yaml|json`) are also read from the deployed copy — editing the repo
   copy alone does nothing until deployed. `bootstrap.py` only *writes* a config file the first
   time it runs; a later `--no-deps` re-mirror leaves an already-deployed `config/*.yaml|json`
   untouched (a machine-local value like `rag.yaml`'s `embedding.model_path` must survive every
   later bootstrap). Use `--force-config` only when you intend to discard local edits to
   `global-policy.yaml`/`rag.yaml`/`mcp-servers.json`/`budgets.yaml`.
3. **Run the tests.** `pytest tests/ -q` (needs `pytest` + `pyyaml`; the platform venv is at
   `~/.claude-env/venv`). Add/extend tests for every behavioral change.
4. **Never weaken the invariants below to make something pass.** They are the product.

## Invariants (the platform's guarantees — preserve them)

- **Persistence is abstracted.** No module imports `sqlite3`/`psycopg` directly — everything goes
  through `lib/db.py::get_db()`. Keep SQL portable (`?` placeholders, standard SQL).
- **Audit ledger is append-only + hash-chained.** Never `UPDATE`/`DELETE` `audit_events` (DB
  triggers reject it). Write via `AuditLogger`; keep `verify_chain()` green. Projections
  (`policy_violations`, `human_approvals`, …) are written in the same transaction as the event.
- **One filesystem chokepoint, fail-closed.** `mcp-servers/filesystem-policy` is the only
  sanctioned path to disk for agents; `security/policy_engine.py` decides. Deny always wins;
  tier-3 is default-deny. The native-tool hooks (`hooks/`) extend the same engine to Read/Write/
  Edit/Bash — including parsing Bash command strings.
- **Retrieved/external text is data, never instructions** — RAG/doc results are delimited and
  poison/injection-screened.
- **No network egress by default.** Local inference only (llama.cpp + ONNX); the sole outbound is
  the tier-gated documentation fetch.
- **Model choice lives in config, never in code.** RAG code always goes through
  `rag.config.get_embedder()` / `get_reranker()`; no model name/path is hardcoded.

## Repo map

```
bootstrap.py            one-command setup: builds the venv (uv-preferred), installs deps,
                        applies sql/, mirrors code + config into $CLAUDE_ENV_HOME
lib/db.py               persistence abstraction (SQLite default, PostgreSQL-ready)
lib/services.py         runtime service registry: UI servers pick a free port + record it
                        ($CLAUDE_ENV_HOME/state/services.json); `claude-env services` lists them
sql/                    001_schema · 002_retention · 003_extensions
security/               policy_engine · detectors · incident · policy_sim
audit/                  audit_logger (hash chain) · compliance_report · session_replay
hooks/                  policy_hook (PreToolUse) · audit_hook (PostToolUse) · install_hooks
config/                 global-policy · repo-policy.template · rag.yaml · mcp-servers.json · budgets
mcp-servers/            filesystem-policy · git · lancedb-rag · memory-graph · terminal · documentation
rag/                    config · chunkers · embeddings · rerankers · retrievers · indexers · pipelines
memory/                 manager · retriever · consolidator · pruner · session_ingestor · sync
agents/                 orchestration/ (approval_gate · approvals_ui) · analysts/nightly_analyst
                        Specialist agents ship as native .claude/agents/*.md files in
                        templates/repo-onboarding/ — task_router · agent_registry · prompts/ retired.
scripts/register_repo.py  the `claude-env onboard`/`register` flow
templates/repo-onboarding/  the CLAUDE.md + skills + agents installed INTO onboarded repos (a deliverable)
                        .claude/agents/ now ships 11 specialist agents (orchestrator + 10 specialists)
tests/                  pytest suite
```

## Common tasks

- **Onboarding / policy / RAG:** `scripts/register_repo.py`, `security/policy_engine.py`,
  `rag/indexers/indexer.py`. RAG scope (`rag.index_paths`) is a subset of policy-allowed files;
  deny always wins; the indexer skips binary files (NUL sniff).
- **MCP servers** use the low-level `mcp` SDK (`Server`, `@server.list_tools`/`call_tool`,
  `stdio_server`). Handlers are `async`. Smoke-test with a real stdio client, not just imports.
- **Terminal approvals:** `mcp-servers/terminal/server.py` opens a `human_approvals` row, opens the
  web UI, and **blocks** until resolved via `agents/orchestration/approvals_ui.py`; approve records
  the OS `user@host`. Only allow-listed / explicitly-approved commands run (argv-only, no shell).
- **Verify the DB / chain** after risky changes:
  `~/.claude-env/venv/bin/python -c "import sys;sys.path.insert(0,'$HOME/.claude-env');from audit.audit_logger import AuditLogger;print(AuditLogger('x',actor='x').verify_chain())"`

## Skills & subagents in this repo

Skills (`.claude/skills/`):
- **`claude-env-development`** — the deploy/test/branch workflow + invariants above.
- **`principled-engineering`** — decoupled architecture, SOLID, simplicity, testability; load it
  for any non-trivial design/feature/refactor so the code stays easy to extend and maintain.
- **`solid-design`** — SOLID applied, with the smell + refactoring for each.
- **`design-patterns`** — pattern selection (and when *not* to), to decouple real axes of change
  without over-engineering.
- **`rag-model-setup`** — checklist for configuring/swapping the RAG embedding or reranker
  model/backend: probing a GGUF's real output dimension and required `pooling_type` before
  editing `rag.yaml`, checking for an existing-index dimension conflict, and validating
  end-to-end before a full re-index. Load when adding a model, switching model family, or
  debugging a dimension/type mismatch during indexing.

Subagents (`.claude/agents/`):
- **`code-reviewer`**, **`architecture-reviewer`**, **`governance-reviewer`** (the last checks the
  security invariants specifically). Use them before declaring non-trivial work done.
- All three are **read-only by tool grant** (`tools: Read, Grep, Glob` — no shell/write). Subagents
  are contained by their `tools:` allow-list + the MCP servers, *not* by their prompts, so never
  grant a reviewer `Bash`/`Write`/`Edit`; `tests/test_subagent_tools.py` enforces this.
