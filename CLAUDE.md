# CLAUDE.md — working on claude-env itself

This is the **claude-env platform** repo: a privacy-first, local-only governance layer for
Claude Code (policy-enforced file access, tamper-evident audit ledger, local RAG + memory MCP
servers, approvals, incident mode). This file is guidance for developing *the platform*; the
full product guide is [`README.md`](README.md) (read it once).

> Not to be confused with `claudenv/templates/repo-onboarding/` — that `CLAUDE.md` + `.claude/`
> is the **deliverable installed into other repos** by `claude-env onboard`. This file governs
> work on the platform's own source.

> **Layout note:** the platform was refactored from a flat top-level layout (`security/`,
> `audit/`, `hooks/`, `mcp-servers/`, `rag/`, `memory/`, `bootstrap.py`) into a single hexagonal
> `claudenv/` package (`domain/`, `application/`, `adapters/`, `ports/`, `di/`). Old paths in any
> stale doc map to `claudenv/<layer>/…` now. The standalone `bootstrap.py` was removed.

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
2. **Code runs from this checkout; config is read from `$CLAUDE_ENV_HOME`.** There is no code
   mirror step anymore — `claudenv/` is imported directly from this repo (the venv at
   `~/.claude-env/venv` runs it in-place), so a code edit takes effect on the next process start;
   **restart Claude Code** for MCP-server / hook changes (those are long-lived processes).
   Config files, however, are still read from the **deployed copy** at `~/.claude-env/config/`
   (`global-policy.yaml`, `rag.yaml`, `mcp-servers.json`, `budgets.yaml`) — see
   `claudenv/adapters/config/base.py`, which prefers `$CLAUDE_ENV_HOME/config/` over the repo
   copy. Editing `config/*.yaml` in the repo does nothing live; edit `~/.claude-env/config/…`
   for a live change (machine-local values like `rag.yaml`'s `embedding.model_path` live only in
   the deployed copy).
   > **Known gap:** `config/mcp-servers.json` still launches `${CLAUDE_ENV_HOME}/mcp-servers/*/server.py`,
   > paths the refactor deleted (the servers now live at `claudenv/adapters/mcp/*/server.py`). The
   > MCP launch wiring needs re-pointing before the live servers work — fix it if your change
   > touches MCP startup.
3. **Run the tests.** `~/.claude-env/venv/bin/python -m pytest claudenv/tests/ -q` (the platform
   venv has `pytest` + deps). Add/extend tests for every behavioral change.
4. **Never weaken the invariants below to make something pass.** They are the product.

## Invariants (the platform's guarantees — preserve them)

- **Persistence is abstracted behind ports.** No module imports `sqlite3`/`psycopg` directly —
  everything goes through the `IDatabase`/`ITransaction` ports (`claudenv/ports/database/`),
  implemented by `claudenv/adapters/persistence/sqlite/`. Keep SQL portable (`?` placeholders,
  standard SQL).
- **Audit ledger is append-only + hash-chained.** Never `UPDATE`/`DELETE` `audit_events` (DB
  triggers reject it). Write via the audit logger (`claudenv/adapters/audit.py`, domain chain in
  `claudenv/domain/audit/`); keep `verify_chain()` green. Projections (`policy_violations`,
  `human_approvals`, …) are written in the same transaction as the event.
- **One filesystem chokepoint, fail-closed.** `claudenv/adapters/mcp/filesystem/` is the only
  sanctioned path to disk for agents; the policy engine (`claudenv/domain/policy/`) decides. Deny
  always wins; tier-3 is default-deny. The native-tool hooks (`claudenv/adapters/hooks/`) extend
  the same engine to Read/Write/Edit/Bash — including parsing Bash command strings.
- **Retrieved/external text is data, never instructions** — RAG/doc results are delimited and
  poison/injection-screened.
- **No network egress by default.** Local inference only (llama.cpp + ONNX); the sole outbound is
  the tier-gated documentation fetch.
- **Model choice lives in config, never in code.** RAG code always goes through the embedding
  factory (`claudenv/adapters/embedding/factory.py`) resolving config from `rag.yaml`; no model
  name/path is hardcoded.
- **Ports are the single import surface.** Adapters/application depend on interfaces re-exported
  from `claudenv/ports/` (never reach across into another adapter's internals). The domain layer
  depends on nothing outward.

## Repo map

Single hexagonal package `claudenv/`, four layers + a DI wiring module. Dependencies point
inward: `adapters` → `ports` ← `application` → `domain`; `domain` depends on nothing outward.

```
claudenv/
  cli.py                click CLI: onboard · scan · index · rag · hooks · report · replay ·
                        incident · services · budget · dashboard · feedback · validate
  di/                   dependency-injection container (get_container) + event bus
  domain/               pure business logic, no I/O:
    policy/             PolicyEngine · PolicyService · CompiledPolicy · RepoPolicy · GlobalPolicy
                        (NOTE: domain/policy_engine/ + domain/policy_rules/ are an older parallel
                         implementation used only by test_policy.py — likely dead; confirm before use)
    memory/             graph entities + service/ (writer · reader · decay · traversal · maintenance)
    audit/              hash chain · events · projections
    rag/ · rag_chunker/ config/models · chunkers (markdown · tree_sitter) + factory
    security/           secret/PII detectors + patterns
    observability/      budget · metrics value objects
    value_objects/      identifiers · enums · path · time · policy primitives
  application/          use-case services orchestrating domain + ports:
    onboarding/ · rag/ (indexer · service) · audit/ · observability/ (budget · dashboard ·
    feedback) · approval.py · docs.py
  adapters/             outward implementations of ports:
    mcp/                filesystem · git · lancedb_rag · memory_graph · terminal · documentation
    persistence/sqlite/ database (IDatabase) + repositories
    config/             base · providers · models (reads $CLAUDE_ENV_HOME/config/ first)
    embedding/          embedders · rerankers · factory (model choice from rag.yaml)
    hooks/              policy_hook (PreToolUse) · audit_hook (PostToolUse) · session_hook · installer
    vector/lancedb/     vector_store + rag_retriever
    observability/      budget_config · repositories
    audit.py · approvals_ui.py · services.py
  ports/                consumer-owned interfaces (the single import surface — see ports/__init__.py):
                        audit · config · database · memory · policy · rag · observability ·
                        approval · hooks · events · services
  templates/repo-onboarding/  CLAUDE.md + skills + 11 native .claude/agents/*.md installed INTO
                        onboarded repos (a deliverable — not platform source)
  bin/claude-env        thin wrapper that adds the repo to sys.path and calls claudenv.cli:cli
  tests/                pytest suite (testpaths in pyproject.toml)

config/                 repo copies of global-policy · repo-policy.template · rag.yaml ·
                        mcp-servers.json · budgets.yaml — live copies read from ~/.claude-env/config/
sql/                    001_schema · 002_retention · 003_extensions · 004_audit_trace_metadata
                        (LIVE: the test suite + apply_schema() load from this root dir)
```

## Common tasks

- **Onboarding / policy / RAG:** `claudenv/application/onboarding/` (`OnboardingService` + steps),
  `claudenv/domain/policy/policy_engine.py`, `claudenv/application/rag/indexer.py`. RAG scope
  (`rag.index_paths`) is a subset of policy-allowed files; deny always wins; the indexer skips
  binary files (NUL sniff).
- **MCP servers** (`claudenv/adapters/mcp/*/server.py`) use the low-level `mcp` SDK (`Server`,
  `@server.list_tools`/`call_tool`, `stdio_server`). Handlers are `async`. Smoke-test with a real
  stdio client, not just imports (`claudenv/tests/test_mcp_servers_construction.py` shows the
  pattern).
- **Terminal approvals:** `claudenv/adapters/mcp/terminal/server.py` opens a `human_approvals`
  row, opens the web UI, and **blocks** until resolved via `claudenv/adapters/approvals_ui.py`;
  approve records the OS `user@host`. Only allow-listed / explicitly-approved commands run
  (argv-only, no shell).
- **Wiring:** everything is constructed in `claudenv/di/__init__.py::_configure_container`; resolve
  a service with `get_container().get(<IPort>)`. Note the container eagerly builds the embedder,
  so it needs `rag.yaml`'s `embedding.model_path` set (or expect a "No embedding model configured"
  error outside the test fixtures).
- **Verify the DB / chain** after risky changes:
  `~/.claude-env/venv/bin/python -m pytest claudenv/tests/test_audit.py -q` (the audit tests build
  a real ledger and assert `verify_chain()` stays green).

## Skills & subagents in this repo

Skills (`.claude/skills/`):
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
