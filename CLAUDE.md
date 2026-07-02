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
   python3 bootstrap.py --no-deps                                                       # re-mirror all + config
   ```
   Config files (`config/*.yaml|json`) are also read from the deployed copy — editing the repo
   copy alone does nothing until deployed.
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
sql/                    001_schema · 002_retention · 003_extensions
security/               policy_engine · detectors · incident · policy_sim
audit/                  audit_logger (hash chain) · compliance_report · session_replay
hooks/                  policy_hook (PreToolUse) · audit_hook (PostToolUse) · install_hooks
config/                 global-policy · repo-policy.template · rag.yaml · mcp-servers.json · budgets
mcp-servers/            filesystem-policy · git · lancedb-rag · memory-graph · terminal · documentation
rag/                    config · chunkers · embeddings · rerankers · retrievers · indexers · pipelines
memory/                 manager · retriever · consolidator · pruner · session_ingestor · sync
agents/                 agent_registry.yaml · prompts/ · orchestration/ (task_router, approval_gate, approvals_ui)
scripts/register_repo.py  the `claude-env onboard`/`register` flow
templates/repo-onboarding/  the CLAUDE.md + skills + agents installed INTO onboarded repos (a deliverable)
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

- Skill **`claude-env-development`** — the deploy/test/branch workflow + invariants above.
- Subagents **`code-reviewer`**, **`architecture-reviewer`**, **`governance-reviewer`** (the last
  checks the security invariants specifically). Use them before declaring non-trivial work done.
