# claude-env

**A privacy-first, local-only AI development platform for Claude Code.** Everything runs
on your machine — local embeddings and reranking (llama.cpp + ONNX), a local LanceDB
vector store, a local SQLite memory graph, and a tamper-evident local audit ledger. No
code, embeddings, or telemetry ever leave the host. Primary target is macOS Apple
Silicon (Metal-accelerated); Linux is supported (CPU inference + systemd timers).

This document gets you from a clean machine to a governed, RAG-indexed repository. For
**why** the platform is built this way (threat model, design rationale), see
[`docs/OVERVIEW.md`](docs/OVERVIEW.md). For **code-level internals** of any subsystem
(config tables, decision-logic walkthroughs, diagrams), see the
[technical guide](docs/guide/README.md).

**The path to a working setup is five steps, in order:** bootstrap the platform once per
machine, install a local embedding model, then onboard each repository you want governed
(onboarding also builds its search index). Every step below is one command — each shows
the exact directory to run it from and the exact output to expect, so you can go through
it once, top to bottom, without guessing.

---

## Table of contents

**Get running**

1. [What you get](#1-what-you-get)
2. [Prerequisites](#2-prerequisites)
3. [Step 1 — Bootstrap the platform](#3-step-1--bootstrap-the-platform)
4. [Step 2 — Install local models](#4-step-2--install-local-models)
5. [Step 3 — Onboard a repository](#5-step-3--onboard-a-repository)
6. [Step 4 — Verify everything works](#6-step-4--verify-everything-works)

**Reference**

7. [repo-policy.yaml reference](#7-repo-policyyaml-reference)
8. [Architecture — where to read more](#8-architecture--where-to-read-more)
9. [On-disk layout](#9-on-disk-layout)
10. [Everyday operations](#10-everyday-operations)
11. [CLI command reference](#11-cli-command-reference)
12. [Troubleshooting](#12-troubleshooting)
13. [Appendix A — JetBrains integration](#appendix-a--jetbrains-integration)
14. [Appendix B — PostgreSQL migration](#appendix-b--postgresql-migration)
15. [Appendix C — Plugin distribution](#appendix-c--plugin-distribution)
16. [License & use](#license--use)

---

## 1. What you get

| Area | What you get |
|---|---|
| **Guardrails** | Layered policy engine (global deny → tier → repo deny → repo allow; deny always wins; tier-3 is default-deny) + content secret-scanning. |
| **Audit** | Append-only, hash-chained ledger; DB triggers reject any mutation; `verify_chain()` proves integrity. |
| **Local RAG** | AST-aware chunking, pluggable local embeddings + optional ONNX reranker, LanceDB hybrid search, one table per repo+branch, automatic re-index on every commit, merge/pull, and branch switch. |
| **Memory graph** | Episodic / semantic / procedural / agent memory in SQLite, with decay, corrections, consolidation, and namespace isolation. |
| **Agents** | 10 specialists + orchestrator with explicit tools, write scopes, and approval gates. |
| **MCP layer** | Six local stdio servers; the policy-enforcing filesystem server is the single sanctioned path to disk. |
| **Native-tool hooks** | Extend the same policy + audit to Claude Code's built-in Read/Write/Edit/Bash tools. |
| **Onboarding** | `claude-env onboard <repo>` — one interactive step provisions an isolated policy, RAG index, memory namespace, and governance `CLAUDE.md`. |
| **Governance ops** | Compliance reports, session replay, an incident kill switch, and policy dry-run/drift. |

Everything runs through a single Python virtual environment at `$CLAUDE_ENV_HOME/venv/`
(default `~/.claude-env/venv/`). The system Python is only ever used once, to run
`bootstrap.py`. The CLI, MCP servers, git hook, and nightly jobs all invoke
`$CLAUDE_ENV_HOME/venv/bin/python` explicitly — you never activate the venv by hand.

---

## 2. Prerequisites

Run these checks before you start. All commands are read-only.

| Check | Command | Expected |
|---|---|---|
| macOS Apple Silicon (Linux also supported) | `uname -m` | `arm64` |
| FileVault enabled | — | On — it encrypts the local store, model weights, and DB |
| Xcode Command Line Tools | `xcode-select -p` | prints a path |
| Homebrew | `brew --version` | any version |
| Python ≥ 3.13 | `python3 --version` | `3.13.x` or newer (`brew install python@3.13` if older) |
| Git ≥ 2.40 | `git --version` | `2.40` or newer |
| Claude Code | `claude --version` | any version |

---

## 3. Step 1 — Bootstrap the platform

Run **once** per machine.

> Run from: this repo's checkout (e.g. `~/Downloads/claude-env-platform`) — `cd` there first.

```bash
python3 bootstrap.py --with-brew
```

This single command:

1. Checks the host meets the prerequisites above.
2. Creates `~/.claude-env/` (override with `--dsn`/`CLAUDE_ENV_HOME`).
3. Creates the venv at `~/.claude-env/venv/` (prefers `uv`, falls back to stdlib `venv`).
4. Installs all runtime deps into that venv (`llama-cpp-python` is built with Metal
   acceleration on Apple Silicon).
5. Applies the SQL schema (`sql/001_schema.sql`, `002_retention.sql`, `003_extensions.sql`).
6. Copies the policy + RAG config into `~/.claude-env/config/`.
7. Mirrors all platform code (`security/`, `rag/`, `memory/`, `hooks/`, …) under
   `~/.claude-env/`.
8. Writes and verifies the first ("genesis") audit event.

Then lock down the store and add the CLI to your shell.

> Run from: anywhere.

```bash
chmod 700 ~/.claude-env

echo 'export CLAUDE_ENV_HOME="$HOME/.claude-env"' >> ~/.zshrc
echo 'export PATH="$CLAUDE_ENV_HOME/bin:$PATH"'   >> ~/.zshrc
source ~/.zshrc
```

**Verify** (run from: anywhere):

```bash
~/.claude-env/venv/bin/python --version   # must print 3.13 or newer
claude-env validate installation
```

This should be green except model warnings, until you complete
[Step 2 — Install local models](#4-step-2--install-local-models) below.

**Useful flags for `bootstrap.py`:**

| Flag | Effect |
|---|---|
| `--no-deps` | Skip pip install; the venv must already exist. |
| `--no-venv-create` | Reuse the existing venv, just update deps. |
| `--recreate-venv` | Delete and rebuild the venv from scratch (e.g. after a Python upgrade). |
| `--dsn postgresql://…` | Target PostgreSQL instead of SQLite (see [Appendix B](#appendix-b--postgresql-migration)). |
| `--force-config` | Reset `global-policy.yaml`/`rag.yaml`/`mcp-servers.json`/`budgets.yaml` to the repo's template, discarding local edits to the deployed copies. Every other run leaves an already-deployed copy untouched — re-running bootstrap (e.g. `--no-deps` after a code change) never reverts your configured `rag.yaml` model path. |

---

## 4. Step 2 — Install local models

**Required before indexing.** The code ships with no model — you download one and point
`config/rag.yaml` at it. The models below are suggestions; any GGUF embedding model and
any ONNX cross-encoder work. **Never use a symlink** — point the config straight at the
real file. All commands in this section run from anywhere.

### 4a. Embedding model (required)

**1. Download the model:**

```bash
mkdir -p ~/.claude-env/models
~/.claude-env/venv/bin/pip install huggingface-hub    # provides the `hf` CLI

hf download nomic-ai/nomic-embed-text-v1.5-GGUF \
  nomic-embed-text-v1.5.Q8_0.gguf \
  --local-dir ~/.claude-env/models
```

This creates `~/.claude-env/models/nomic-embed-text-v1.5.Q8_0.gguf`.

**2. Point the deployed config at it.** Edit the file at
`~/.claude-env/config/rag.yaml` — this is the copy the servers actually read, not the
repo's copy — so it looks like this:

```yaml
# ~/.claude-env/config/rag.yaml
embedding:
  model_path: "~/.claude-env/models/nomic-embed-text-v1.5.Q8_0.gguf"
  embedding_dim: 768                     # MUST match your model (nomic = 768)
  pooling_type: "mean"                   # mean|cls|last|none — must match how the GGUF pools
  document_prefix: "search_document: "   # nomic needs task prefixes; leave empty if yours doesn't
  query_prefix:    "search_query: "
```

> [!TIP]
> **`pooling_type` matters more than it looks.** If a GGUF doesn't bake in
> pooling, leaving this wrong (or `"none"` when the model needs pooling) makes
> the embedder return one vector *per token* instead of one per input — this
> surfaces as a confusing dimension/type error during indexing, not a clean
> pooling error. Before trusting a model card's claim, probe it directly (see
> the `rag-model-setup` skill, or [§12 troubleshooting](#12-troubleshooting)).

**3. Verify:**

```bash
claude-env validate installation
# "embedding model file present" must show PASS
```

Suggested (not required) settings: 768-dim GGUF at `Q8_0` quantization (`Q4`/`Q5` degrade
recall); context window 2048; chunk sizes 512 tokens for code, 384 for markdown, 256 for
sections, ~12% overlap.

### 4b. Reranker (optional)

Improves result ordering; RAG works without it (degrades gracefully to fusion order).
Commands in this section also run from anywhere.

**1. Download the ONNX cross-encoder:**

```bash
hf download cross-encoder/ms-marco-MiniLM-L6-v2 \
  --include "onnx/model.onnx" "onnx/model_qint8_arm64.onnx" \
            "tokenizer.json" "config.json" "vocab.txt" \
            "tokenizer_config.json" "special_tokens_map.json" \
  --local-dir ~/.claude-env/models/reranker-onnx/
```

**2. Move the quantized (fastest on Apple Silicon) file into the location the loader
expects — `model.onnx` in the directory root:**

```bash
mv ~/.claude-env/models/reranker-onnx/onnx/model_qint8_arm64.onnx \
   ~/.claude-env/models/reranker-onnx/model.onnx
```

**3. Point the deployed config at the directory** — edit
`~/.claude-env/config/rag.yaml`:

```yaml
# ~/.claude-env/config/rag.yaml
reranker:
  model_dir: "~/.claude-env/models/reranker-onnx"   # or "" to disable
```

**4. Verify:**

```bash
claude-env validate installation
# "reranker loads + runs" should show PASS (WARN means disabled/absent — RAG still works)
```

### 4c. Choosing / swapping models (no code changes)

Resolution order: **environment variable > `config/rag.yaml` > numeric fallback.**

| Variable | Meaning | Default |
|---|---|---|
| `EMBED_BACKEND` | Which embedder backend to use (registry key, `rag/embeddings/registry.py`) | `llama_cpp` |
| `EMBED_MODEL_PATH` | Path to the `.gguf` file | **required** — unset raises a clear error |
| `EMBED_MODEL_NAME` | Provenance label recorded in `rag_index_state` | filename |
| `EMBED_DOC_PREFIX` / `EMBED_QUERY_PREFIX` | Per-task input prefixes | `""` |
| `EMBED_POOLING_TYPE` | Pooling applied to raw model output: `mean`\|`cls`\|`last`\|`none` | `mean` |
| `EMBED_CTX` / `EMBED_GPU_LAYERS` / `EMBED_DIM` | Context window / GPU offload / vector dim | `2048` / `-1` / `768` |
| `RERANKER_BACKEND` | Which reranker backend to use (registry key, `rag/rerankers/registry.py`) | `onnx_cross_encoder` |
| `RERANKER_DIR` | ONNX cross-encoder directory (`""` disables) | `""` |
| `LANCEDB_PATH` | Vector store location | `~/.claude-env/knowledge/lancedb` |

Both `backend` fields select an implementation registered in
`rag/embeddings/registry.py` / `rag/rerankers/registry.py` — the platform ships
one built-in backend per slot (`llama_cpp`, `onnx_cross_encoder`); adding another
is a code change behind those registries, not a config value that already exists
today. See `docs/guide/rag-pipeline.md#swapping-backends-not-just-models`.

Check either model is live: `claude-env validate installation` (the "embedding model
file present" and "reranker loads + runs" lines) or in Python:

```bash
~/.claude-env/venv/bin/python -c "from rag.config import get_reranker; print(get_reranker().status())"
# ok: True means reranking is live
```

> [!WARNING]
> **Vectors from different models/dimensions are not comparable.** After swapping the
> embedding model, drop the affected LanceDB tables and re-index — see
> [Troubleshooting: "Removing a repo's RAG index"](#12-troubleshooting). Gate the swap
> with `claude-env rag-bench` (recall@k + MRR) before rolling it out.

> [!IMPORTANT]
> **Always edit the deployed config, not the repo's.** The servers and tools read
> `~/.claude-env/config/rag.yaml`, which is what [§4a](#4a-embedding-model-required)/[§4b](#4b-reranker-optional)
> above edit directly. Bootstrap only writes this file the *first* time it runs (it
> never overwrites an already-deployed copy, so your model path survives every later
> `bootstrap.py --no-deps`) — if you edit the repo's `config/rag.yaml` by mistake, copy
> it over by hand (`cp config/rag.yaml ~/.claude-env/config/rag.yaml`) rather than
> re-running bootstrap, since `--force-config` would also reset `global-policy.yaml`,
> `mcp-servers.json`, and `budgets.yaml` back to their repo templates.

---

## 5. Step 3 — Onboard a repository

Onboarding is the one command that turns a plain repo into a governed one: an isolated
policy, RAG index, memory namespace, and a governance `CLAUDE.md` all get provisioned
together.

**1. Run onboarding**, pointing at the repo's absolute path — interactive is recommended
the first time.

> Run from: anywhere — `claude-env` resolves its own venv regardless of your current directory.

```bash
claude-env onboard /absolute/path/to/repo
```

Or non-interactively, accepting all detected defaults:

```bash
claude-env onboard /absolute/path/to/repo --yes
```

**2. Answer the prompts** (each has a detected default — press Enter to accept it):

```text
  Repo slug (used for RAG + memory namespaces) [my-repo]:
  Privacy tier — 0 public / 1 internal / 2 sensitive / 3 restricted [1]: 2
  Default branch [main]:
  Short description (optional) []: billing service
```

**3. Onboarding then provisions the repo end to end:**

| Step | What happens |
|---|---|
| Policy | Writes `<repo>/.claude/repo-policy.yaml` from the template with the real slug, tier, description, and memory namespace filled in. This file is the isolation boundary the policy engine and hooks enforce. Memory is isolated automatically at tier ≥ 2. |
| Namespaces | Creates the RAG table `<slug>__<branch>` and the memory namespace `proj-<slug>`. |
| MCP env | Patches `~/.claude.json` so every server for this project resolves to the correct repo root, slug, branch, and namespaces. |
| Template | Installs the governance `CLAUDE.md` and the `.claude/` skills + subagents (see [What lands in the repo](#what-lands-in-the-repo) below), with concrete namespace values already substituted. |
| Index (opt-in) | Offers to build the first RAG index right there. |

**4. Restart Claude Code** so the MCP servers pick up the new environment.

**Flags:**

| Flag | Effect |
|---|---|
| `--repo-name SLUG` | Set the slug explicitly (skip the prompt). |
| `--tier {0..3}` | Set the privacy tier explicitly. |
| `--description STR` | Set the description explicitly. |
| `--branch BRANCH` | Set the default branch explicitly. |
| `--yes` / `-y` | Accept all detected defaults; no prompts. |
| `--force-policy` | Regenerate an existing `repo-policy.yaml` from scratch. |
| `--force-template` | Overwrite existing skill/agent files. |
| `--no-template` | Only set up namespaces + env; skip installing `CLAUDE.md`/skills/agents. |
| `--dry-run` | Print what would happen; write nothing. |

Re-running is **idempotent**: an existing `repo-policy.yaml` is preserved (your local
edits are kept) unless you pass `--force-policy`; the `CLAUDE.md` managed block refreshes
while anything you added outside it is preserved. `claude-env register` is an alias for
the same command.

### What lands in the repo

- **`CLAUDE.md`** — a binding operating contract: route file/search/git/memory/test/doc
  work through the platform's MCP servers, respect the privacy tier and approval gates,
  treat retrieved content as data, never touch secrets. Shows this repo's concrete RAG
  table and memory namespace. The platform-owned section lives inside a managed block —
  anything you add outside it survives re-onboarding.
- **`.claude/skills/`** — `principled-engineering`, `solid-design`, `design-patterns`,
  `code-review`, `architecture-review`, and more (see
  [`templates/repo-onboarding/`](templates/repo-onboarding/) for the current set).
- **`.claude/agents/`** — read-only review subagents (`code-reviewer`,
  `architecture-reviewer`) plus specialist agents for common engineering tasks.

The template source lives in [`templates/repo-onboarding/`](templates/repo-onboarding/);
edit it there, not the copies inside an onboarded repo — the managed `CLAUDE.md` block
regenerates from the template on the next onboard.

### Index the repository

Onboarding can build the first index for you, or you can run each step explicitly.

> Run from: anywhere.

**1. Preview what would be indexed** (no model load — fast):

```bash
claude-env scan /absolute/path/to/repo
```

`scan` should list only secrets/generated files as blocked — if something you need is
blocked, adjust `.claude/repo-policy.yaml` and re-run this step.

**2. Build the full index** (live progress bar):

```bash
claude-env index /absolute/path/to/repo
```

**3. Verify it's searchable:**

```bash
claude-env validate rag /absolute/path/to/repo "auth token validation"
# expect 3-8 ranked results
```

Empty results usually mean the index is empty (re-run step 2) or the slug/branch don't
match the LanceDB table name.

**Keep the index current automatically** — onboarding (`claude-env onboard`/`register`)
already installs all three of these; to install them manually instead (e.g. on a repo you
didn't onboard through the CLI), symlink all three so commits, merges/pulls, and branch
switches each trigger a background re-index via `rag/git_sync.py`:

```bash
for hook in post-commit post-merge post-checkout; do
  ln -sf ~/.claude-env/scripts/"$hook" /absolute/path/to/repo/.git/hooks/"$hook"
  chmod +x /absolute/path/to/repo/.git/hooks/"$hook"
done
```

### Extend governance to native tools (Read/Write/Edit/Bash)

Onboarding installs this automatically. To (re)install or preview it manually:

> Run from: the repo's root directory — without `--repo`, `claude-env hooks` targets
> `<current directory>/.claude/settings.json`. Pass `--repo /path/to/repo` to target a
> different repo without `cd`-ing into it.

```bash
claude-env hooks              # (re)install into THIS repo's .claude/settings.json
claude-env hooks --dry-run    # preview the resulting settings.json, write nothing
claude-env hooks --global     # machine-wide install instead (legacy; opt-in)
```

To remove them again (mirrors whichever target is in effect):

```bash
claude-env hooks --uninstall
claude-env hooks --uninstall --global
```

Restart Claude Code, then confirm it's working: ask Claude to read a blocked file (e.g.
`.env`) — the call must be denied with the matched rule. On tier-2+ repos, also set
`CLAUDE_ENV_HOOK_FAIL_CLOSED=true` in the environment Claude Code runs under. Full
decision logic is documented in
[`docs/guide/native-tool-hooks.md`](docs/guide/native-tool-hooks.md).

---

## 6. Step 4 — Verify everything works

After restarting Claude Code, confirm each server by asking Claude to use it:

| Server | Ask Claude | Expected |
|---|---|---|
| `git` | "what files are modified?" | Lists modified files. |
| `filesystem-policy` | "list files in src/" | A file listing. |
| `lancedb-rag` | "search the codebase for X" | Ranked chunks. |
| `memory-graph` | "what do you remember?" | Memory nodes (may be empty on first run). |
| `terminal` | "run the tests" | Runs the configured tests, or opens a gated approval. |
| `documentation` | "search docs for X" | Local doc matches. |

**Full install health check** (run from: anywhere):

```bash
claude-env validate installation
```

**Confirm RAG returns ranked results** (run from: anywhere):

```bash
claude-env validate rag /absolute/path/to/repo "some real query"
```

> [!NOTE]
> `fatal: not a git repository`, or empty RAG results with a real index present, almost
> always mean a missing/wrong `env` block in `~/.claude.json` — re-run
> `claude-env onboard /absolute/path/to/repo` and restart Claude Code.

---

## 7. repo-policy.yaml reference

Lives at `<repo>/.claude/repo-policy.yaml`; created by
[onboarding](#5-step-3--onboard-a-repository). Deny always wins over allow; at tier 3
the allow list is authoritative — anything not explicitly allowed is denied.

```yaml
version: 1
tier: 1                       # 0 public · 1 internal · 2 sensitive · 3 restricted
repo: "my-repo-slug"          # stable slug; drives RAG + memory namespacing
description: "..."

allow:
  paths:      ["src/**", "docs/**", "ADRs/**", "tests/**", "*.md"]
  extensions: [".py", ".ts", ".go", ".java", ".rs", ".md", ".sql"]

deny:                         # always evaluated; always wins
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
  isolated: false             # tier >= 2 forces true (no cross-project reads)
  share_with_agents: [architect, backend, testing, documentation]

agent_permissions:            # per-agent override of registry defaults for THIS repo
  devops: { requires_approval: true }
```

Preview a candidate policy before applying it:

```bash
claude-env policy-sim simulate /repo --candidate new-policy.yaml
```

---

## 8. Architecture — where to read more

This README covers installation and day-to-day operation. For everything else:

| You want to know... | Read |
|---|---|
| **Why** the platform enforces things this way — the threat model behind each guardrail | [`docs/OVERVIEW.md`](docs/OVERVIEW.md) |
| Exact config tables, decision-logic walkthroughs, and diagrams for a specific subsystem (policy engine, RAG pipeline, memory graph, hooks, MCP servers, agents, approvals, incident mode, ...) | [`docs/guide/README.md`](docs/guide/README.md) — one doc per module |
| How registering MCP servers at user scope works, and why you usually don't need to | [`docs/guide/mcp-servers.md`](docs/guide/mcp-servers.md) |

In short: two enforcement surfaces (the MCP servers, and the native-tool hooks) both
consult one policy engine and write one append-only, hash-chained audit ledger, over
local knowledge stores and local inference. The only network egress is the
tier-gated documentation fetch. Nothing else about the design lives in this file anymore
— the docs above are kept current with the code and go much deeper than a README should.

---

## 9. On-disk layout

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

Source-tree layout of this repository — see [`CLAUDE.md`](CLAUDE.md) for the annotated,
kept-current version used when developing the platform itself.

---

## 10. Everyday operations

### Memory maintenance

Set the venv Python once, then run whichever maintenance step you need:

```bash
PY=~/.claude-env/venv/bin/python
```

| Step | Command |
|---|---|
| Integrity check + safe repair | `$PY memory/memory_validator.py --all --repair` |
| Merge low-value clusters | `$PY memory/memory_consolidator.py --all` |
| Prune (dry-run report) | `$PY memory/memory_pruner.py --all` |
| Prune (actually apply, archives first) | `$PY memory/memory_pruner.py --namespace proj-x --apply` |

> [!NOTE]
> Pruning archives to `~/.claude-env/archive/memory/<ns>.jsonl` and never prunes
> `decision` / `architecture` nodes.

### Approvals

Governance has two enforcement layers:

- **`terminal.run` opens a human approval and blocks on it.** A free-form/state-mutating
  command opens a pending approval, plays a notification sound (disable with
  `CLAUDE_ENV_APPROVAL_SOUND=false`), auto-opens the approvals web UI, and waits (up to
  `CLAUDE_ENV_APPROVAL_WAIT_S`, default 120s) until you Approve/Deny. The decision
  records the OS `user@host` automatically. On approve, the command runs in the same
  sandbox (no real shell — chaining/piping via `;`/`&&`/`||`/`|` is supported without
  one, scrubbed env, timeout) — cwd defaults to the repo root, or pass
  `in_scratch: true` to start it in this repo's disposable scratch directory
  (`$CLAUDE_ENV_HOME/scratch/<repo>/`, the same directory `filesystem.read/write/list`
  reach via `scratch://` paths) instead.
- **Hard-deny (no gate)** — `git push`/`amend`/`rebase`/`reset --hard`, and the
  native-tool hooks ([§5](#extend-governance-to-native-tools-readwriteeditbash)) simply
  refuse rather than opening an approval.

A gate opens when any of:

- the agent requires approval by default (e.g. `devops`)
- the repo is tier 2/3
- a write targets a path outside the agent's scope
- a state-mutating terminal command
- a git history rewrite/push
- a destructive memory op
- the action is in the agent's `denied_tools` list

**See what's on the local UI servers:**

```bash
claude-env services   # which local UI is on which port (auto-assigned if the default is busy)
```

**List and open the approval queue:**

```bash
claude-env approvals --list-open  # terminal list
claude-env approvals-ui           # web UI (Approve/Deny)
```

**Resolve a specific pending approval:**

```bash
claude-env approvals-ui --port 8002 --by you
# or resolve one directly, no browser needed:
claude-env approvals --resolve <id> --approve --by you
```

Full walkthrough: [`docs/guide/approvals-workflow.md`](docs/guide/approvals-workflow.md).

### Observability & budgets

**Dashboard:**

```bash
claude-env dashboard --window 7d
claude-env dashboard serve --port 8001    # Datasette (read-only, localhost)
```

**Budgets:**

```bash
claude-env budget                # per-repo monthly USD; exit 1 if exceeded
claude-env budget --format json
```

Set limits in `~/.claude-env/config/budgets.yaml`. Update `PRICES` in
`observability/collectors.py` to current per-1M-token pricing for accurate cost tracking.

### Governance: compliance, replay, incident, policy-sim

**Compliance reports** — markdown evidence + chain proof:

```bash
claude-env report --window 30d
claude-env report --window 7d --repo payments --format csv --out evidence.csv
```

> [!TIP]
> A report's integrity line is the first thing to check — a non-`VERIFIED` chain exits 1.

**Session replay** — step-by-step forensics timeline:

```bash
claude-env replay --list          # recent sessions
claude-env replay <session_id>    # step-by-step timeline
```

**Incident mode** — the kill switch; fails every policy evaluation closed:

```bash
claude-env incident on --reason "suspected token leak" --by you
claude-env incident status
claude-env incident off --by you
```

**Policy simulation** — dry-run a candidate policy or diff against the template:

```bash
claude-env policy-sim simulate /repo --candidate new-policy.yaml
claude-env policy-sim diff /repo/.claude/repo-policy.yaml ~/.claude-env/config/repo-policy.template.yaml
```

### Knowledge & quality tools

| Tool | Command | What it does |
|---|---|---|
| Fused recall | `claude-env know "jwt validation" --repo payments --repo-root /work/payments` | Memory + RAG + git recall with provenance. |
| Context pack | `claude-env context-pack /work/payments --write` | Generates `CLAUDE.generated.md` from repo signals + memory. |
| RAG benchmark | `claude-env rag-bench /work/payments --create-template` | Scaffolds a benchmark template — edit it, then run it. |
| Test impact | `claude-env test-impact /work/payments --since HEAD~1` | Changed files → minimal test set. |
| Doc drift | `claude-env doc-drift /work/payments` | Finds markdown referencing code that moved/changed. |
| Digest | `claude-env digest /work/payments` | Runs the analyst digest right now. |

For nightly per-repo digests, list repo paths (one per line) in
`~/.claude-env/config/analyst-repos.txt`.

### Team knowledge sync

**On your machine — export** (secret-redacted):

```bash
claude-env memory-sync export --namespace proj-payments --out team.jsonl
```

Review the JSONL, then share it with your teammate.

**On the teammate's machine — import** (additive; `--namespace` remaps if needed):

```bash
claude-env memory-sync import --in team.jsonl
```

> [!NOTE]
> Imports are additive — existing node ids are skipped. Review the export before
> sharing, regardless.

### Backup & recovery

**Backup** (schedule daily):

```bash
H=~/.claude-env
sqlite3 "$H/state/claude-env.db" ".backup '$H/archive/db-$(date +%F).sqlite'"   # online, WAL-safe
rsync -a "$H/knowledge/lancedb/" "$H/archive/lancedb-$(date +%F)/"
```

> [!NOTE]
> The venv does **not** need backup — rebuild it with `claude-env bootstrap --no-venv-create`.

**Recovery:**

```bash
H=~/.claude-env
cp "$H/archive/db-YYYY-MM-DD.sqlite" "$H/state/claude-env.db"
rsync -a --delete "$H/archive/lancedb-YYYY-MM-DD/" "$H/knowledge/lancedb/"
claude-env validate installation    # must exit 0
```

Recovery is complete only when `validate_installation.py` exits 0 **and**
`verify_chain()` returns `True`.

### Upgrades

> [!IMPORTANT]
> Always snapshot the DB ([Backup & recovery](#backup--recovery) above) before
> upgrading; restore on failure.

**Schema change** — add `sql/00N_*.sql` (bump `schema_version`), then apply it:

```bash
~/.claude-env/venv/bin/python -c "
import sys; sys.path.insert(0, '.')
from lib.db import get_db
get_db().apply_schema('sql/00N_whatever.sql')
"
```

**Code + dependencies:**

```bash
git pull
claude-env bootstrap --no-venv-create   # update deps in the existing venv
```

**Full venv rebuild** (e.g. after a Python upgrade):

```bash
claude-env bootstrap --recreate-venv
```

### Nightly automation

The nightly job ingests session transcripts, runs memory maintenance, and writes
per-repo digests.

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

## 11. CLI command reference

`claude-env <command>` dispatches through the venv Python automatically. Run
`claude-env` (or `claude-env help`) for a grouped overview, `claude-env help <command>`
for a one-line summary, and `claude-env <command> --help` for a command's full options.

| Command | Purpose |
|---|---|
| `bootstrap` | Run `bootstrap.py`. |
| `onboard` / `register` | Interactive repo onboarding (policy + namespaces + env + template). |
| `scan` | Preview policy allow/block split for a repo. |
| `index` / `reindex` | Full / incremental RAG index. |
| `validate [all\|installation\|security\|rag\|memory\|agents\|mcp\|features]` | Run validators. |
| `hooks` | Install/preview/uninstall native-tool hooks. |
| `know` | Fused memory + RAG + git recall with provenance. |
| `context-pack` | Generate `CLAUDE.generated.md` from repo signals + memory. |
| `rag-bench` / `test-impact` / `doc-drift` / `digest` | Quality tools. |
| `route` | Route a task to a specialist agent. |
| `approvals` / `approvals-ui` | List / resolve approval gates. |
| `services` | List the live local UI servers and their (possibly auto-assigned) ports. |
| `report` / `replay` | Compliance evidence / session forensics. |
| `incident` | Kill switch (on/off/status). |
| `policy-sim` | Candidate-policy dry run / drift diff. |
| `dashboard` | Metrics summary / Datasette UI. |
| `budget` | Per-repo monthly cost budgets. |
| `ingest-sessions` / `memory-sync` | Transcript ingest / namespace export-import. |

---

## 12. Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| `validate_installation` fails on venv | Bootstrap not run | `claude-env bootstrap` |
| MCP server won't start | venv Python not in command | Ensure `claude mcp add` used `$H/venv/bin/python`, not bare `python`. |
| `git` MCP: `fatal: not a git repository` | `CLAUDE_ENV_REPO_ROOT` unset | `claude-env onboard <repo>`; restart Claude Code. |
| `filesystem-policy`: "file not found" | Wrong/unset repo root | Same as above. |
| `lancedb-rag` returns empty | Wrong `CLAUDE_ENV_REPO_NAME`/`BRANCH` | Check `ls ~/.claude-env/knowledge/lancedb/`; re-onboard; restart. |
| `memory.recall` always `[]` | Nodes have `embedding=NULL` | Re-write nodes after model config, or back-fill (below). |
| RAG quality dropped / dim mismatch | Embedding model dimension changed | Vectors aren't comparable — drop tables + re-index (below). |
| Agent "BLOCKED" on a normal file | Over-broad deny rule | Inspect `policy_violations`; adjust repo `deny`/`allow` (never global). |
| Reranker not improving results | ONNX absent/failed/disabled | `python -c "from rag.config import get_reranker; print(get_reranker().status())"` — `ok` should be `True`. |
| Audit chain reports broken | Manual edit / partial restore | Restore from a known-good backup; never edit `audit_events`. |
| Memory leaks across repos | `CLAUDE_ENV_MEMORY_ISOLATED` unset for tier ≥ 2 | Set tier ≥ 2 in repo policy and re-onboard. |
| Terminal command "GATED" | State-mutating command | Route via `approval_gate.py`; only test/bench/audit run unattended. |
| Every native tool call denied | Incident mode active | `claude-env incident status`; `claude-env incident off --by you`. |
| Bash command denied unexpectedly | It touches a denied path / network egress | See [`docs/guide/native-tool-hooks.md`](docs/guide/native-tool-hooks.md); use an allowed path or request approval. |
| Hooks not firing | Settings not installed / stale session | `claude-env hooks` then restart Claude Code. |

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

**Back-filling memory embeddings** (only if nodes were written with `embedding=NULL`):
iterate `memory_nodes WHERE embedding IS NULL`, embed `name + body_json` with
`rag.config.get_embedder()`, and `UPDATE … SET embedding=?`.

Logs live in `~/.claude-env/logs/` (`incremental_index.log`, `memory.log`, `digests/`).

**Verify the audit chain any time:**

```bash
~/.claude-env/venv/bin/python -c "import sys; sys.path.insert(0,'$HOME/.claude-env'); \
from audit.audit_logger import AuditLogger; print(AuditLogger('ops',actor='ops').verify_chain())"
# (True, None) on a clean ledger
```

---

## Appendix A — JetBrains integration

Target IDEs: IntelliJ IDEA Ultimate, PyCharm Professional, WebStorm, GoLand, Rider, CLion.

**Setup:** install the **Claude Code** plugin from the JetBrains Marketplace (it bundles
the MCP bridge on recent builds) → `Settings → Tools → Claude Code`: point it at your
`claude` CLI, enable "Use project MCP configuration", and set the project env
(`CLAUDE_ENV_REPO_ROOT=$ProjectFileDir$`, tier, `CLAUDE_ENV_MEMORY_NS=proj-<slug>`). Do
**not** install third-party "AI" plugins that phone home — this platform is local-only.

**Recommended IDE settings:** Actions-on-Save → Reformat + Optimize imports (so agent
diffs match house style); keep language inspections strict (the review agents consume
them via the bridge); enable "Run Git hooks" on commit, merge, and checkout (so the
RAG re-index hooks fire on all three — see [§10](#10-everyday-operations)); mark
`generated/`, `vendor/`, `node_modules/` as Excluded.

**Workflows:** *Code review* — open the diff, ask Claude to review; the orchestrator
routes to Security + Performance (read-only) + Testing; findings are file:line
clickable and security ones land in `security_events`. *Refactor* — the
Backend/Frontend agent proposes a diff within its `write_paths`; prefer IDE-native
Rename/Extract for mechanical steps; run tests before accepting. *Architecture review* —
ask the Architect for a module review; output is an ADR/RFC, not code. *Debug* — give
the failure context; agents localize via `git.blame`/RAG, propose a fix + a regression
test, and store the investigation as a memory node.

**Security:** the IDE bridge respects the same `filesystem-policy` chokepoint — a file
blocked by the repo policy is not surfaced even if open in the editor. No separate IDE
credential store; no outbound calls beyond the tier-gated documentation fetch.

---

## Appendix B — PostgreSQL migration

SQLite is the default and is enough for a single machine. Migrate to PostgreSQL for
multi-machine sharing, concurrent writers, or centralized audit retention. Because all DB
access goes through `lib/db.py`, application code does not change.

**Already portable:** `?` placeholders (rewritten to `%s` for psycopg), the
`WITH RECURSIVE` memory traversal, `ON CONFLICT … DO UPDATE` upserts, and `struct`
float32 embedding BLOBs (→ `BYTEA`).

**Dialect deltas** (in a new `sql/00N_pg.sql`): `INTEGER PRIMARY KEY AUTOINCREMENT` →
`BIGSERIAL`; drop SQLite `PRAGMA`s (use server config); `TEXT` timestamps →
`TIMESTAMPTZ` (or keep ISO text for byte-identical audit payloads); `BLOB` → `BYTEA`;
re-express the append-only `BEFORE UPDATE/DELETE` triggers as PL/pgSQL raising an
exception; views port unchanged.

**Cutover (offline):** `createdb claude_env` → apply the PG schema via
`get_db().apply_schema(...)` → **copy `audit_events` first, in ascending `event_id`
order** (preserve `event_id`, `prev_hash`, `event_hash` verbatim — never recompute),
then projections, memory, metrics → run `verify_chain()` on PostgreSQL (must print
`(True, None)`) → `setval(...)` the identity sequences → set
`CLAUDE_ENV_DSN=$PGDSN` in shell/launchd/MCP env → re-run the validators. LanceDB is
unaffected (replicate the dir for multi-machine). **Rollback:** the SQLite file is
read-only during migration — point `CLAUDE_ENV_DSN` back at it and re-validate.

**Concurrency:** the audit writer takes a process lock for monotonic chaining; with
multiple machines writing one PostgreSQL ledger, wrap read-prev-hash + insert in a
`SERIALIZABLE` transaction (retry on failure) or funnel audit writes through a single
writer service. Memory and metrics tolerate concurrent writers as-is.

---

## Appendix C — Plugin distribution

`claude-plugin/` packages the governance layer for a team: the policy + audit hooks
(`hooks/hooks.json`), the core MCP servers (`.mcp.json`), and slash commands
(`/claude-env:know`, `/claude-env:report`, `/claude-env:replay`).

**Install from a marketplace:**

```text
/plugin marketplace add <org>/claude-env
/plugin install claude-env
```

**Or, for local testing:**

```text
claude --plugin-dir /path/to/claude-env/claude-plugin
```

> [!NOTE]
> The platform must still be [bootstrapped](#3-step-1--bootstrap-the-platform) and
> [models configured](#4-step-2--install-local-models) on each machine — the plugin is
> only the wiring/distribution layer. See `claude-plugin/README.md`. Without the plugin
> system, [`claude-env hooks`](#extend-governance-to-native-tools-readwriteeditbash)
> achieves the same wiring.

---

## License & use

Internal engineering starter. Review `config/global-policy.yaml` and the tier matrix
against your organization's actual data-handling requirements before relying on this for
regulated data. This is a reference implementation, not a certified compliance product.
