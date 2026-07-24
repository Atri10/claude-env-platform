# claude-env

**A privacy-first, local-only AI governance platform for Claude Code.** Everything runs
on your machine — local embeddings (llama.cpp + ONNX), a local LanceDB vector store, a
local SQLite memory graph, and a tamper-evident local audit ledger. No code, embeddings,
or telemetry ever leave the host. Primary target is macOS Apple Silicon (Metal-accelerated);
Linux is supported (CPU inference).

For **why** the platform is built this way, see [`docs/OVERVIEW.md`](docs/OVERVIEW.md).
For **code-level internals** of any subsystem, see the
[technical guide](docs/guide/README.md).

---

## Table of contents

**Get running**
1. [What you get](#what-you-get)
2. [Prerequisites](#prerequisites)
3. [Step 1 — Install the platform](#step-1--install-the-platform)
4. [Step 2 — Configure models](#step-2--configure-models)
5. [Step 3 — Onboard a repository](#step-3--onboard-a-repository)
6. [Step 4 — Verify everything works](#step-4--verify-everything-works)

**Reference**
7. [repo-policy.yaml reference](#repo-policyyaml-reference)
8. [CLI command reference](#cli-command-reference)
9. [Architecture — where to read more](#architecture--where-to-read-more)
10. [Everyday operations](#everyday-operations)
11. [Troubleshooting](#troubleshooting)

---

## Installation

```bash
pip install claudenv
claude-env init
```

That provisions `~/.claude-env/` with the SQL schema, default config, and genesis
audit event. Everything else is subcommand-driven. The platform ships as a
pip-installable Python package — no `bootstrap.py`, no manual venv management,
no code mirroring.

After install, configure a local model:
```bash
claude-env model ensure    # one-click recommended model download + config
```

Then onboard a repository:
```bash
claude-env onboard /path/to/repo
```

---

## What you get

| Area | What you get |
|---|---|
| **Guardrails** | Multi-tier policy engine (global deny → repo rules; deny always wins). Content secret-scanning on Read/Write/Edit. |
| **Audit** | Append-only, hash-chained ledger with DB-level mutation guards. `verify_chain()` proves integrity. |
| **Local RAG** | AST-aware chunking, pluggable local embeddings + ONNX reranker, LanceDB hybrid search. Incremental indexing — only changed files reindexed. |
| **Memory graph** | Episodic / semantic / procedural / agent memory with decay, consolidation, and namespace isolation. |
| **MCP layer** | Six local stdio servers; the policy-enforcing filesystem server is the single sanctioned path to disk. |
| **Native-tool hooks** | Extend the same policy + audit to Claude Code's Read/Write/Edit/Bash tools. |
| **Onboarding** | `claude-env onboard <repo>` provisions an isolated policy, RAG index, memory namespace, and governance `CLAUDE.md`. |
| **Operations** | Compliance reports, session replay, incident kill switch, policy dry-run, cost budgets, dashboard. |

---

## Prerequisites

| Check | Command | Expected |
|---|---|---|
| macOS Apple Silicon (Linux also supported) | `uname -m` | `arm64` |
| Python >= 3.13 | `python3 --version` | `3.13.x` or newer |
| Git >= 2.40 | `git --version` | `2.40` or newer |
| Claude Code | `claude --version` | any version |
| pip | `pip --version` | any recent version |

---

## Step 1 — Install the platform

```bash
pip install claudenv       # or: pip install -e . from source checkout
claude-env init             # provisions ~/.claude-env/
```

What `init` does:
1. Creates `~/.claude-env/{state,config,knowledge/lancedb,logs}`
2. Copies packaged default config files into `~/.claude-env/config/`
3. Applies the SQL schema (single-file `schema.sql`, idempotent)
4. Writes and verifies the genesis audit event

**Flags:**
| Flag | Effect |
|---|---|
| `--force-config` | Overwrite existing config files with packaged defaults |
| `--interactive` / `-i` | After init, run interactive model setup |

Deployed config (`~/.claude-env/config/`) always takes precedence over the packaged
default. Edit config there, not in the repo's `_data/` directory.

---

## Step 2 — Configure models

**One-click recommended setup:**
```bash
claude-env model ensure
```
This downloads the recommended all-MiniLM-L6-v2 (~90 MB) and configures `rag.yaml`.

**Manual setup:**
```bash
claude-env model setup
```
Walk through embedding model path, pooling type, dimension, and reranker directory.
All values can also be set via environment variables (see `rag.yaml` comments).

**Or download a GGUF model manually:**
```bash
mkdir -p ~/.claude-env/models
pip install huggingface-hub
huggingface-cli download nomic-ai/nomic-embed-text-v1.5-GGUF \
  nomic-embed-text-v1.5.Q8_0.gguf --local-dir ~/.claude-env/models
```
Then edit `~/.claude-env/config/rag.yaml`:
```yaml
embedding:
  model_path: "~/.claude-env/models/nomic-embed-text-v1.5.Q8_0.gguf"
  embedding_dim: 768
  pooling_type: "mean"
  document_prefix: "search_document: "
  query_prefix: "search_query: "
```

**Verify:**
```bash
claude-env validate installation
```

---

## Step 3 — Onboard a repository

```bash
claude-env onboard /path/to/repo
```

Interactive prompts guide you through repo slug, privacy tier (0-3), description,
and default branch. Non-interactive: `--yes` accepts all defaults.

Onboarding provisions:
| Step | What happens |
|---|---|
| Policy | Writes `<repo>/.claude/repo-policy.yaml` — the isolation boundary |
| Namespaces | RAG table `<slug>__<branch>` and memory namespace `proj-<slug>` |
| MCP env | Patches `~/.claude.json` for server resolution |
| Template | Installs governance `CLAUDE.md` + `.claude/skills/` + `.claude/agents/` |
| Index (opt-in) | Builds the first RAG index |

**Indexing afterwards:**
```bash
claude-env index /path/to/repo          # incremental — only changed files
claude-env index /path/to/repo --full   # force full rebuild
claude-env scan /path/to/repo            # preview policy allow/block split
```

**Native tool hooks:**
```bash
claude-env hooks /path/to/repo          # install into .claude/settings.json
```

Restart Claude Code after onboarding or hooks install so MCP servers pick up changes.

---

## Step 4 — Verify everything works

```bash
claude-env validate installation
```

| Server | Ask Claude | Expected |
|---|---|---|
| `git` | "what files are modified?" | Lists modified files |
| `filesystem-policy` | "list files in src/" | A file listing |
| `lancedb-rag` | "search the codebase for X" | Ranked chunks |
| `memory-graph` | "what do you remember?" | Memory nodes |
| `terminal` | "run the tests" | Runs tests or opens approval gate |
| `documentation` | "search docs for X" | Local doc matches |

---

## repo-policy.yaml reference

Lives at `<repo>/.claude/repo-policy.yaml`. Created by onboarding. Deny always wins.

```yaml
version: 1
tier: 1                       # 0=public, 1=internal, 2=sensitive, 3=restricted
repo: "my-repo-slug"
description: "..."

allow:
  paths:      ["src/**", "docs/**", "tests/**", "*.md"]
  extensions: [".py", ".ts", ".go", ".java", ".rs", ".md", ".sql"]

deny:                         # always evaluated; always wins
  paths:      ["**/secrets/**", "**/.ssh/**", "**/.env"]
  extensions: [".pem", ".key", ".crt", ".keystore"]
  regex:      [{pattern: '(^|/)\.env($|\.)', reason: "dotenv files"}]

content_scan:
  enabled: true
  on_match: "redact"          # redact | block

rag:
  enabled: true
  index_paths:   ["src/**", "docs/**"]
  exclude_paths: ["**/node_modules/**", "**/dist/**"]

memory:
  namespace: "proj-my-repo-slug"
  isolated: false             # tier >= 2 forces true
```

Preview a candidate policy before applying:
```bash
claude-env policy-sim simulate /repo --candidate new-policy.yaml
```

---

## CLI command reference

Full reference: **[docs/cli-reference.md](docs/cli-reference.md)** — every command, flag, argument, default, exit code, and example.

`claude-env <command>` — every command has `--help` for full options.

| Command | Purpose |
|---|---|
| `init` | Provision `~/.claude-env` (one-time, idempotent) |
| `onboard <repo>` | Interactive repo onboarding |
| `scan <repo>` | Preview policy allow/block split |
| `index <repo>` | Build / update RAG index (incremental by default) |
| `rag <repo> <query>` | Test RAG retrieval |
| `hooks <repo>` | Install native-tool governance hooks |
| `model setup` | Manual model configuration (writes rag.yaml) |
| `model ensure` | One-click recommended model download + config |
| `validate installation` | Health check |
| `report` | Generate compliance report |
| `replay` | Session forensics |
| `incident on\|off\|status` | Kill switch |
| `services` | List running local UI services |
| `budget` | Per-repo monthly cost against limits |
| `dashboard` | Read-only operational summary |
| `feedback` | RAG retrieval feedback stats |
| `policy-sim simulate` | Dry-run candidate policy |

---

## Architecture — where to read more

The platform is built with Clean Architecture (hexagonal ports-and-adapters):

```
claudenv/
  domain/          pure business logic — no I/O, no framework imports
  ports/           consumer-owned interfaces (the single import surface)
  application/     use-case orchestration (depends on ports, not adapters)
  adapters/        outward implementations (SQLite, LanceDB, MCP, hooks, etc.)
  di/              dependency-injection container (single wiring point)
  cli/             Click command line (thin dispatcher)
```

| You want to know... | Read |
|---|---|
| **Why** the platform enforces things this way | [`docs/OVERVIEW.md`](docs/OVERVIEW.md) |
| Code-level internals of any subsystem | [`docs/guide/README.md`](docs/guide/README.md) |
| Developing the platform itself | [`CLAUDE.md`](CLAUDE.md) |

---

## Everyday operations

### Observability & budgets

```bash
claude-env dashboard --window 7d          # operational summary
claude-env budget                          # per-repo monthly spend
claude-env budget --format json
claude-env feedback --repo payments        # RAG retrieval quality
```

Set limits in `~/.claude-env/config/budgets.yaml`.

### Governance

```bash
claude-env report --window 30d                         # compliance report
claude-env report --window 7d --format csv --out e.csv
claude-env replay --list                               # recent sessions
claude-env replay <session_id>                         # timeline
claude-env incident on --reason "suspected leak"       # kill switch
claude-env incident off
claude-env policy-sim simulate /repo --candidate new-policy.yaml
```

### Approvals

```bash
claude-env services           # which local UI is on which port
python -m claudenv.adapters.approvals_ui --port 8002   # web UI
```

Full walkthrough: [`docs/guide/approvals-workflow.md`](docs/guide/approvals-workflow.md).

### Memory

```bash
# CLI-driven operations available via:
python -m claudenv.adapters.mcp.memory_graph
```

Full walkthrough: [`docs/guide/memory-graph.md`](docs/guide/memory-graph.md).

---

## Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| `claude-env init` fails | Python < 3.13 or missing deps | Check prerequisites; `pip install -e .` from source |
| Index returns empty | No model configured | `claude-env model ensure` |
| MCP server won't start | Wrong venv or stale env | `claude-env onboard <repo>` then restart Claude Code |
| `fatal: not a git repository` | `CLAUDE_ENV_REPO_ROOT` unset | `claude-env onboard <repo>` |
| RAG quality dropped | Model dimension changed | Delete LanceDB tables, re-index |
| Agent blocked on normal file | Over-broad deny rule | Check `policy_violations`; adjust repo policy |
| Audit chain broken | Manual edit / partial restore | Restore from backup; never edit `audit_events` |
| Every tool call denied | Incident mode active | `claude-env incident status` |
| Hooks not firing | Settings not installed | `claude-env hooks <repo>` then restart Claude Code |

**Removing a repo's RAG index:**
```bash
rm -rf ~/.claude-env/knowledge/lancedb/${SLUG}__${BRANCH}.lance/
claude-env init   # re-apply config, then claude-env index <repo>
```

**Verify the audit chain:**
```bash
.claude-env/venv/bin/python -m pytest claudenv/tests/test_audit.py -q
```

**Logs:** `~/.claude-env/logs/claudenv.log` — rotates daily, 7-day retention.

---

## License

MIT. See [`LICENSE`](LICENSE).
