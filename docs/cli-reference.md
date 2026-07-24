# CLI Command Reference

Full reference for every `claude-env` command, subcommand, flag, argument, and
exit code. Run `claude-env --help` for a compact overview; this doc covers every
option and its behavior.

---

## Global flags

| Flag | Effect |
|---|---|
| `-v`, `--verbose` | Enable `DEBUG`-level logging to both console and file. |

All subcommands inherit these flags through `claude-env <subcommand> --verbose`.

---

## init

Idempotent one-time provisioning of `~/.claude-env`.

```
claude-env init [--force-config] [--interactive]
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `--force-config` | bool | `false` | Overwrite deployed config files with packaged defaults |
| `-i`, `--interactive` | bool | `false` | After init, run interactive model setup |

**What it does:**

1. Creates `~/.claude-env/{state,config,knowledge/lancedb,logs}`
2. Copies packaged config files to `~/.claude-env/config/` (skips existing unless `--force-config`)
3. Applies `claudenv/_data/sql/schema.sql` (idempotent — all `CREATE IF NOT EXISTS`)
4. Writes and verifies the genesis audit event

**Exit code:** 1 if audit chain verification fails after genesis.

**Examples:**
```bash
claude-env init                         # first-time setup
claude-env init --force-config          # reset config to packaged defaults
claude-env init --interactive           # setup + interactive model configuration
```

---

## onboard

Onboard a repository — provisions policy, namespaces, template, and hooks.

```
claude-env onboard [REPO_ROOT] [options]
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `--repo-name` | str | auto-detected | Namespace-safe slug (e.g. `payments-api`) |
| `--tier` | choice `0,1,2,3` | auto-detected | Privacy tier: 0=public, 1=internal, 2=sensitive, 3=restricted |
| `--description` | str | `""` | Short description written into `repo-policy.yaml` |
| `--branch` | str | auto-detected | Default branch for RAG indexing |
| `-i`, `--interactive` | bool | `false` | Force interactive prompts even if `--yes` |
| `-y`, `--yes` | bool | `false` | Accept all defaults (non-interactive) |
| `--dry-run` | bool | `false` | Print what would happen without writing |
| `--no-template` | bool | `false` | Skip installing CLAUDE.md, skills, agents |
| `--force-template` | bool | `false` | Overwrite existing skill/agent files |
| `--force-policy` | bool | `false` | Regenerate `repo-policy.yaml` from template |
| `--no-post-commit` | bool | `false` | Skip git hook installation |

**What it provisions:**

| Step | Files created |
|---|---|
| Policy | `<repo>/.claude/repo-policy.yaml` |
| MCP env | Patches `~/.claude.json` with repo root, slug, branch |
| Template | `<repo>/CLAUDE.md`, `.claude/skills/`, `.claude/agents/` |
| Hooks | `.claude/settings.json` with PreToolUse/PostToolUse/Session hooks |
| Index (opt-in) | Offers to build first RAG index |

**Exit code:** 0 on success/abort, 1 if `repo_root` missing after interactive flow.

**Examples:**
```bash
claude-env onboard /path/to/repo                    # interactive
claude-env onboard /path/to/repo --yes              # non-interactive, accept defaults
claude-env onboard /path/to/repo --dry-run          # preview without writing
claude-env onboard /path/to/repo -y --tier 2        # non-interactive, tier 2
claude-env onboard /path/to/repo --no-template      # policy + env only, no template
```

---

## model

Model configuration subcommands.

```
claude-env model <subcommand>
```

### model setup

Manual configuration — walks through embedding model, pooling type,
dimension, and reranker directory.

```
claude-env model setup [options]
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `--model-path` | str | from `rag.yaml` | Path to GGUF embedding model file |
| `--pooling-type` | choice `mean,cls,last,none` | from `rag.yaml` | Pooling applied to raw model output |
| `--embedding-dim` | int | from `rag.yaml` | Output vector dimension (must match model) |
| `--reranker-dir` | str | from `rag.yaml` | Directory containing ONNX cross-encoder |
| `-y`, `--yes` | bool | `false` | Non-interactive, use provided/default values |

Writes the configured values into `~/.claude-env/config/rag.yaml`.

**Example:**
```bash
claude-env model setup \
  --model-path ~/.claude-env/models/nomic-embed-text-v1.5.Q8_0.gguf \
  --embedding-dim 768 --pooling-type mean --yes
```

### model ensure

One-click setup — checks if a model is configured, and if not, downloads and
configures the recommended default.

```
claude-env model ensure [--yes]
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `-y`, `--yes` | bool | `false` | Skip confirmation prompt |

Downloads all-MiniLM-L6-v2 (~90 MB) and configures `rag.yaml` for ONNX
backend. If a model is already configured, prints the current config and
exits immediately.

**Exit code:** 1 if download fails.

**Example:**
```bash
claude-env model ensure       # prompts for confirmation
claude-env model ensure --yes # skip confirmation
```

---

## scan

Preview what files would be indexed vs. blocked by the policy engine.

```
claude-env scan REPO_ROOT
```

| Argument | Type | Required | Description |
|---|---|---|---|
| `REPO_ROOT` | path | yes | Path to repository root |

Prints total candidate count, allowed count, blocked count, and the first
20 blocked files with the matching rule. Does not load the embedding model.

**Exit code:** 1 if platform not initialized.

**Example:**
```bash
claude-env scan /path/to/repo
# Candidates: 847  Allowed: 842  Blocked: 5
# Blocked:
#   .env: deny extension .env
#   secrets/config.yaml: deny path **/secrets/**
```

---

## index

Build or update the RAG index. **Incremental by default** — only changed files
are re-indexed on subsequent runs.

```
claude-env index REPO_ROOT [--branch BRANCH] [--full]
```

| Argument | Type | Required | Description |
|---|---|---|---|
| `REPO_ROOT` | path | yes | Path to repository root |

| Flag | Type | Default | Description |
|---|---|---|---|
| `--branch` | str | auto-detected | Branch to index |
| `--full` | bool | `false` | Force complete rebuild (ignore prior index state) |

**Incremental mode (default):** On first run, indexes all files. On subsequent
runs, uses `git diff` from the last indexed commit to HEAD to find only
changed files. Prints "Index up to date" when nothing has changed.

**Full mode:** Reads all files tracked by git, chunks, embeds, and upserts
into the LanceDB vector store. Shows a progress bar during file reading.

**Exit code:** 1 if not initialized or not onboarded.

**Examples:**
```bash
claude-env index /path/to/repo              # incremental (or full on first run)
claude-env index /path/to/repo --full       # force complete rebuild
claude-env index /path/to/repo --branch dev # index a specific branch
```

---

## rag

Test RAG retrieval with a natural language query.

```
claude-env rag REPO_ROOT QUERY [--branch BRANCH]
```

| Argument | Type | Required | Description |
|---|---|---|---|
| `REPO_ROOT` | path | yes | Path to repository root |
| `QUERY` | str | yes | Natural language search query |

| Flag | Type | Default | Description |
|---|---|---|---|
| `--branch` | str | auto-detected | Branch to query |

Returns up to 5 ranked results with file paths, line ranges, relevance
scores, and the first 200 characters of each chunk.

**Exit code:** 1 if not initialized or not onboarded.

**Example:**
```bash
claude-env rag /path/to/repo "how does authentication work"
```

---

## hooks

Install native-tool governance hooks (PreToolUse + PostToolUse + SessionStart/SessionEnd).

```
claude-env hooks REPO_ROOT
```

| Argument | Type | Required | Description |
|---|---|---|---|
| `REPO_ROOT` | path | yes | Path to repository root |

Writes hook entries into `<repo>/.claude/settings.json`. Installs four hooks:
- `PolicyHook` — PreToolUse (Read, Write, Edit, Bash path/command validation)
- `AuditHook` — PostToolUse (audits mutating tools)
- `SessionHook` — SessionStart/SessionEnd (session lifecycle audit)
- `SessionMetricsHook` — SessionStart/SessionEnd (cost tracking)

**Exit code:** 1 if installation fails.

**Example:**
```bash
claude-env hooks /path/to/repo
```

---

## report

Generate a compliance report with audit chain verification.

```
claude-env report [--window WINDOW] [--repo REPO] [--format FORMAT] [--out PATH]
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `--window` | str | `7d` | Time window: `24h`, `7d`, `30d` |
| `--repo` | str | all repos | Filter to one repository |
| `--format` | choice `markdown,csv` | `markdown` | Output format |
| `--out` | path | stdout | Write report to file |

Includes: event counts, policy violations, approval decisions, security
events, and the audit chain integrity line.

**Example:**
```bash
claude-env report --window 30d
claude-env report --window 7d --repo payments --format csv --out evidence.csv
```

---

## replay

Replay session forensics — browse audit events by session.

```
claude-env replay [SESSION_ID] [--list]
```

| Argument | Type | Required | Description |
|---|---|---|---|
| `SESSION_ID` | str | no | Session UUID to replay |

| Flag | Type | Default | Description |
|---|---|---|---|
| `--list` | bool | `false` | List recent sessions |

**Examples:**
```bash
claude-env replay --list                     # show recent sessions
claude-env replay <session-id>               # step-by-step timeline
```

---

## incident

Incident mode kill switch — instantly denies all agent operations.

```
claude-env incident {on|off|status} [--reason TEXT] [--by TEXT]
```

| Argument | Type | Required | Description |
|---|---|---|---|
| `ACTION` | choice `on,off,status` | yes | Activate, deactivate, or check incident mode |

| Flag | Type | Default | Description |
|---|---|---|---|
| `--reason` | str | `""` | Reason for arming incident mode |
| `--by` | str | `"operator"` | Operator identity recorded in audit |

**Behavior:**
- `on` — Denies all in-flight approvals, writes incident marker, logs critical audit event.
- `off` — Removes incident marker, logs lift event.
- `status` — Prints whether incident mode is active, with activation time and reason.

**Examples:**
```bash
claude-env incident on --reason "suspected token leak in .env" --by alice
claude-env incident status
claude-env incident off --by alice
```

---

## services

List running local UI services and their ports.

```
claude-env services
```

Reads `~/.claude-env/state/services.json`. Prints `name: url (pid=pid)` for
each registered service.

**Example:**
```bash
claude-env services
# approvals: http://127.0.0.1:8002 (pid=12345)
```

---

## budget

Check month-to-date spend against configured budgets.

```
claude-env budget [--repo REPO] [--format FORMAT]
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `--repo` | str | all repos | Filter to one repository |
| `--format` | choice `text,json` | `text` | Output format |

Prints a table of per-repo spend, budget cap, percentage, and status
(`ok` / `WARNING` / `EXCEEDED` / `unlimited`). Budgets are configured in
`~/.claude-env/config/budgets.yaml`.

**Exit code:** 1 if any repo exceeds its budget.

**Examples:**
```bash
claude-env budget                          # all repos, table
claude-env budget --repo payments          # single repo
claude-env budget --format json            # machine-readable
```

---

## dashboard

Read-only operational summary — costs, latency, retrieval quality,
violations, security events, and open approvals.

```
claude-env dashboard [--window WINDOW] [--format FORMAT]
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `--window` | str | `30d` | Time window: `24h`, `7d`, `30d` |
| `--format` | choice `text,json` | `text` | Output format |

**Text mode sections:** Top session costs, cost by repo, latency by component
(p50/p95/max), retrieval quality by repo, recent policy violations,
security events, open approvals.

**Examples:**
```bash
claude-env dashboard --window 7d
claude-env dashboard --window 24h --format json
```

---

## feedback

Show RAG retrieval feedback statistics.

```
claude-env feedback [--repo REPO]
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `--repo` | str | all repos | Filter to one repository |

Prints total `retrieved` count, total `used` count, and the top used
files (most frequently cited after retrieval).

**Example:**
```bash
claude-env feedback --repo payments
```

---

## validate

Run validation checks.

```
claude-env validate <subcommand>
```

### validate installation

Health check — verifies the platform installation.

```
claude-env validate installation
```

Checks:
1. `venv` directory exists
2. `config` directory exists
3. `global-policy.yaml` present
4. `rag.yaml` present
5. `mcp-servers.json` present
6. `knowledge/lancedb` directory exists

Prints `PASS` or `FAIL` for each check.

**Exit code:** 1 if any check fails.

**Example:**
```bash
claude-env validate installation
#   [PASS] venv exists
#   [PASS] config dir
#   [PASS] global policy
#   [PASS] rag.yaml
#   [PASS] mcp-servers.json
#   [PASS] knowledge dir
# All checks passed!
```

---

## policy-sim

Dry-run a candidate policy before deploying it.

```
claude-env policy-sim <subcommand>
```

### policy-sim simulate

Evaluate every tracked file against a candidate policy and report changes.

```
claude-env policy-sim simulate REPO_ROOT --candidate CANDIDATE_YAML
```

| Argument | Type | Required | Description |
|---|---|---|---|
| `REPO_ROOT` | path | yes | Path to repository root |

| Flag | Type | Default | Description |
|---|---|---|---|
| `--candidate` | path (file) | **required** | Candidate `repo-policy.yaml` to evaluate |

Reports:
- Tier change (current → candidate)
- Total file count
- Blocked count change (current → candidate)
- Newly blocked files (up to 20, with deny rule)
- Newly allowed files (up to 20, with allow rule)
- Files blocked by a different rule (up to 20)

**Example:**
```bash
claude-env policy-sim simulate /path/to/repo --candidate new-policy.yaml
```

---

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Error — check stderr for details |

| Command | Exit 1 condition |
|---|---|
| `init` | Audit chain verification fails |
| `onboard` | `repo_root` missing after interactive prompts |
| `index` | Not initialized or not onboarded |
| `rag` | Not initialized or not onboarded |
| `scan` | Not initialized |
| `hooks` | Installation reported as failed |
| `budget` | Any repo exceeded its budget |
| `model ensure` | Model download failed |
| `validate installation` | Any check failed |
| `model setup` | Provided model path does not exist (non-interactive) |

**Auto-init:** Commands that need an initialized home (`scan`, `index`, `rag`)
will offer to run `claude-env init` automatically if the DB is missing.
Declining exits with code 1.

---

## Environment variables

| Variable | Used by | Default |
|---|---|---|
| `CLAUDE_ENV_HOME` | All commands | `~/.claude-env` |
| `CLAUDE_ENV_DSN` | Database commands | `sqlite:///$CLAUDE_ENV_HOME/state/claude-env.db` |
| `CLAUDE_ENV_REPO_ROOT` | MCP servers, hooks | (set by onboarding) |
| `CLAUDE_ENV_HOOK_FAIL_CLOSED` | Policy hook | `false` |

Model configuration via environment variables — see
`~/.claude-env/config/rag.yaml` for the full list (`EMBED_MODEL_PATH`,
`EMBED_DIM`, `EMBED_POOLING_TYPE`, `RERANKER_DIR`, etc.).
