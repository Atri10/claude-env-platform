# claude-env — Operational Runbook

> **Read `README.md` before this document.** This runbook covers day-2 ops,
> initial model setup, MCP registration, and per-repo onboarding in enough
> detail that a new operator can go from zero to all-green without prior
> knowledge of the platform.

---

## 0. Prerequisites

Before touching the platform, confirm every item below on the host machine.

| Check | Command | Expected result |
|---|---|---|
| macOS Apple Silicon | `uname -m` | `arm64` |
| Xcode CLT | `xcode-select -p` | a path (not an error) |
| Homebrew | `brew --version` | any version |
| Python ≥ 3.13 | `python3 --version` | `Python 3.13.x` or higher |
| Git ≥ 2.40 | `git --version` | `git version 2.40+` |
| Claude Code CLI | `claude --version` | any version |

If `python3 --version` returns something older than 3.13, install via
`brew install python@3.13` and verify again.

---

## 1. One-time bootstrap

**Action required — run once on a new machine.**

```bash
# Clone or copy this repo to a convenient location
cd /path/to/claude-env

# Bootstrap: creates ~/.claude-env/, venv, installs all Python deps,
# initialises the SQLite database, sets up policy files, and writes the
# genesis audit event. Pass --with-brew to also install llama.cpp and
# sqlite via Homebrew.
python3 bootstrap.py --with-brew

# Lock down the directory — all local model weights, the database, and
# audit logs live here; world-readable would be a security risk.
chmod 700 ~/.claude-env

# Add to shell profile (do this once, then open a new terminal)
echo 'export CLAUDE_ENV_HOME="$HOME/.claude-env"' >> ~/.zshrc
echo 'export PATH="$CLAUDE_ENV_HOME/bin:$PATH"'   >> ~/.zshrc
source ~/.zshrc
```

Confirm bootstrap succeeded:

```bash
~/.claude-env/venv/bin/python --version   # must print 3.13+
claude-env validate installation          # all checks green (model warnings OK for now)
```

> **What bootstrap does:**
> 1. Checks macOS arm64, Python ≥ 3.13, Homebrew, git
> 2. Creates `~/.claude-env/` directory layout (state/, knowledge/, models/, logs/, archive/)
> 3. Creates `~/.claude-env/venv/` — an isolated Python venv never touching system site-packages
> 4. Installs all runtime deps into the venv (pyyaml, lancedb, llama-cpp-python with Metal, onnxruntime, mcp, …)
> 5. Applies `sql/001_schema.sql` and `sql/002_retention.sql` to `~/.claude-env/state/claude-env.db`
> 6. Copies `config/global-policy.yaml` and `repo-policy.template.yaml` to `~/.claude-env/config/`
> 7. Writes the genesis audit event and verifies hash-chain integrity

---

## 2. Local model preparation

**Action required — do this before indexing any repository.**

All inference runs locally. Models are downloaded once and never re-fetched
at request time.

### 2a. Embedding model — nomic-embed-text-v1.5 Q8_0 GGUF

```bash
mkdir -p ~/.claude-env/models/embedding

# Option A — via huggingface-cli (recommended)
~/.claude-env/venv/bin/pip install huggingface-hub   # if not already installed
huggingface-cli download nomic-ai/nomic-embed-text-v1.5-GGUF \
  nomic-embed-text-v1.5.Q8_0.gguf \
  --local-dir ~/.claude-env/models/embedding

# Option B — manual
# 1. Open https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF in a browser
# 2. Download nomic-embed-text-v1.5.Q8_0.gguf
# 3. Move it: mv ~/Downloads/nomic-embed-text-v1.5.Q8_0.gguf ~/.claude-env/models/embedding/

# Create the symlink that llama_embedder.py expects by default
ln -sf ~/.claude-env/models/embedding/nomic-embed-text-v1.5.Q8_0.gguf \
       ~/.claude-env/models/nomic-embed-text-v1.5.Q8_0.gguf

# Verify
ls -lh ~/.claude-env/models/nomic-embed-text-v1.5.Q8_0.gguf
# expected: ~140 MB file or symlink
```

Install the Python binding with Metal GPU acceleration into the venv:

```bash
CMAKE_ARGS="-DLLAMA_METAL=on" \
  ~/.claude-env/venv/bin/pip install --upgrade --force-reinstall llama-cpp-python
```

> `bootstrap.py` runs this automatically on Apple Silicon. Re-run the pip
> install after any Python version upgrade or llama.cpp update.

### 2b. Reranker — ms-marco-MiniLM-L-6-v2 ONNX

```bash
huggingface-cli download cross-encoder/ms-marco-MiniLM-L-6-v2 \
  --include "*.onnx" "*.json" "*.txt" \
  --local-dir ~/.claude-env/models/reranker-onnx/
```

> The reranker degrades gracefully to identity ordering if absent
> (`CrossEncoderReranker.ok == False`), so RAG still works while the model
> is downloading.

Verify both models load:

```bash
claude-env validate installation
# llama_cpp and onnxruntime lines should now show PASS
```

---

## 3. Registering MCP servers with Claude Code

**Action required — once per machine, then once per new project.**

### 3a. Global registration

Register all six servers globally so they are available to every Claude Code
session. Use `$H/venv/bin/python` explicitly — Claude Code launches servers
outside any active shell, so bare `python` would not resolve to the venv.

```bash
H=~/.claude-env
PY="$H/venv/bin/python"

claude mcp add filesystem-policy -- "$PY" "$H/mcp-servers/filesystem-policy/server.py"
claude mcp add git               -- "$PY" "$H/mcp-servers/git/server.py"
claude mcp add lancedb-rag       -- "$PY" "$H/mcp-servers/lancedb-rag/server.py"
claude mcp add memory-graph      -- "$PY" "$H/mcp-servers/memory-graph/server.py"
claude mcp add terminal          -- "$PY" "$H/mcp-servers/terminal/server.py"
claude mcp add documentation     -- "$PY" "$H/mcp-servers/documentation/server.py"
```

Verify:

```bash
claude mcp list
# should show all six servers
```

### 3b. Per-project environment variables — auto-configure (CRITICAL)

The global `claude mcp add` commands above register servers with an empty `env`
block, meaning each server inherits `os.getcwd()` as its repo root at startup.
**This is wrong** — Claude Code launches MCP servers from its own working
directory, not the project root. Every server that uses `CLAUDE_ENV_REPO_ROOT`
will silently operate on the wrong directory until this is fixed.

**Fix:** run the `register` command once per repo. It reads `config/mcp-servers.json`,
resolves all env vars for the given repo, and patches `~/.claude.json` automatically:

```bash
# Basic usage — repo-name defaults to dirname, branch auto-detected from git HEAD
claude-env register /absolute/path/to/your-repo

# Explicit repo-name and branch (use when dirname differs from the LanceDB index slug)
claude-env register /absolute/path/to/your-repo \
  --repo-name your-repo-slug \
  --branch main

# Preview what would be written without touching ~/.claude.json
claude-env register /absolute/path/to/your-repo --dry-run
```

Then **restart Claude Code** so the servers pick up the new environment.

---

**Manual alternative:** if you prefer to edit `~/.claude.json` by hand, locate
the section for your project:

```json
"projects": {
  "/absolute/path/to/your-repo": {
    "mcpServers": { ... }
  }
}
```

Replace the empty `"env": {}` blocks with the values below. Use your actual
username and repo path everywhere `<<NAME>>` and `<<REPO_PATH>>` appear.

```json
"filesystem-policy": {
  "type": "stdio",
  "command": "/Users/<<NAME>>/.claude-env/venv/bin/python",
  "args": ["/Users/<<NAME>>/.claude-env/mcp-servers/filesystem-policy/server.py"],
  "env": {
    "CLAUDE_ENV_REPO_ROOT": "<<REPO_PATH>>"
  }
},
"git": {
  "type": "stdio",
  "command": "/Users/<<NAME>>/.claude-env/venv/bin/python",
  "args": ["/Users/<<NAME>>/.claude-env/mcp-servers/git/server.py"],
  "env": {
    "CLAUDE_ENV_REPO_ROOT": "<<REPO_PATH>>"
  }
},
"lancedb-rag": {
  "type": "stdio",
  "command": "/Users/<<NAME>>/.claude-env/venv/bin/python",
  "args": ["/Users/<<NAME>>/.claude-env/mcp-servers/lancedb-rag/server.py"],
  "env": {
    "CLAUDE_ENV_REPO_ROOT": "<<REPO_PATH>>",
    "CLAUDE_ENV_REPO_NAME": "<<REPO_SLUG>>",
    "CLAUDE_ENV_BRANCH":    "<<DEFAULT_BRANCH>>"
  }
},
"memory-graph": {
  "type": "stdio",
  "command": "/Users/<<NAME>>/.claude-env/venv/bin/python",
  "args": ["/Users/<<NAME>>/.claude-env/mcp-servers/memory-graph/server.py"],
  "env": {
    "CLAUDE_ENV_REPO_ROOT":       "<<REPO_PATH>>",
    "CLAUDE_ENV_MEMORY_NS":       "proj-<<REPO_SLUG>>",
    "CLAUDE_ENV_MEMORY_ISOLATED": "false"
  }
},
"terminal": {
  "type": "stdio",
  "command": "/Users/<<NAME>>/.claude-env/venv/bin/python",
  "args": ["/Users/<<NAME>>/.claude-env/mcp-servers/terminal/server.py"],
  "env": {
    "CLAUDE_ENV_REPO_ROOT": "<<REPO_PATH>>"
  }
},
"documentation": {
  "type": "stdio",
  "command": "/Users/<<NAME>>/.claude-env/venv/bin/python",
  "args": ["/Users/<<NAME>>/.claude-env/mcp-servers/documentation/server.py"],
  "env": {
    "CLAUDE_ENV_REPO_ROOT": "<<REPO_PATH>>",
    "CLAUDE_ENV_DOCS_DIR":  "/Users/<<NAME>>/.claude-env/knowledge/docs"
  }
}
```

**Variable reference:**

| Variable | What to put | Example |
|---|---|---|
| `<<NAME>>` | macOS username | `jsmith` |
| `<<REPO_PATH>>` | Absolute path to repo | `/Users/jsmith/code/my-project` |
| `<<REPO_SLUG>>` | Short identifier for the repo (matches `repo` field in `.claude/repo-policy.yaml`) | `my-project` |
| `<<DEFAULT_BRANCH>>` | Default branch name as it appears in the LanceDB index filename | `main` or `trunk` |

> **How to find `<<DEFAULT_BRANCH>>`:** After indexing the repo (section 4),
> run `ls ~/.claude-env/knowledge/lancedb/`. The index filename is
> `<<REPO_SLUG>>__<<BRANCH>>.lance`. Use the branch part of that filename.

**After editing `~/.claude.json`, restart Claude Code** so the MCP servers
are re-launched with the new environment.

> **Why `CLAUDE_ENV_REPO_ROOT` matters for each server:**
> - `filesystem-policy` — resolves all file paths relative to this root; wrong root → "file not found" on every read
> - `git` — runs `git -C $CLAUDE_ENV_REPO_ROOT`; wrong root → `fatal: not a git repository`
> - `lancedb-rag` — uses `CLAUDE_ENV_REPO_NAME` + `CLAUDE_ENV_BRANCH` to select the correct LanceDB table; wrong values → empty search results
> - `terminal` — runs test/audit commands with this as cwd
> - `documentation` — uses `CLAUDE_ENV_DOCS_DIR` to locate the local docs corpus

---

## 4. Onboarding a repository

**Action required — once per repository.**

### 4a. Create repo policy

```bash
cd /path/to/your-repo
mkdir -p .claude
cp ~/.claude-env/config/repo-policy.template.yaml .claude/repo-policy.yaml
```

Open `.claude/repo-policy.yaml` and set at minimum:

```yaml
version: 1
tier: 1                    # 0=unrestricted, 1=standard, 2=sensitive, 3=default-deny
repo: your-repo-slug       # short identifier; must match CLAUDE_ENV_REPO_NAME
description: "..."
```

Review the allow/deny/rag sections and adjust for your project. The template
is annotated — read each comment before changing a value.

Optionally copy the `.claudeignore` template to reduce noise in RAG:

```bash
cp ~/.claude-env/scripts/.claudeignore.template .claudeignore
```

### 4b. Preview the policy split (no model load)

```bash
claude-env scan /path/to/your-repo
# or:
~/.claude-env/venv/bin/python ~/.claude-env/rag/repository_scan.py /path/to/your-repo
```

This prints how many files would be indexed vs blocked. Check that the blocked
list contains only secrets/generated files and nothing that should be searchable.
Adjust `.claude/repo-policy.yaml` if needed and re-run until satisfied.

### 4c. Index the repository

```bash
claude-env index /path/to/your-repo
# or:
~/.claude-env/venv/bin/python ~/.claude-env/rag/bootstrap_rag.py /path/to/your-repo
```

Progress is shown in the terminal as a live bar:

```
Indexing your-repo (3421 files) ...
[################------------------------] 412/3421 clients/src/main/java/...
Done: 287 indexed, 3134 unchanged, 14203 chunks in 184.3s
```

The final JSON summary is printed to stdout; progress goes to stderr so
piping the JSON works cleanly.

### 4d. Verify RAG is working

```bash
claude-env validate rag /path/to/your-repo "auth token validation"
# or:
~/.claude-env/venv/bin/python ~/.claude-env/rag/validate_rag.py \
  /path/to/your-repo "auth token validation"
```

Expect 3–8 results ranked by semantic relevance. Empty results mean either
the index is empty (re-run step 4c) or the branch/repo name in the LanceDB
table doesn't match (check `CLAUDE_ENV_REPO_NAME` and `CLAUDE_ENV_BRANCH`).

### 4e. Install the post-commit hook

```bash
ln -sf ~/.claude-env/scripts/post-commit /path/to/your-repo/.git/hooks/post-commit
chmod +x /path/to/your-repo/.git/hooks/post-commit
```

From this point, every `git commit` in the repo triggers an incremental
re-index in the background. The hook is non-blocking and appends to
`~/.claude-env/logs/incremental_index.log`.

### 4f. Configure MCP env vars for this repo

```bash
claude-env register /path/to/your-repo
# restart Claude Code after this step
```

See section 3b for `--repo-name` / `--branch` overrides and the manual
alternative.

---

## 5. Verifying all MCPs after restart

After restarting Claude Code, confirm each server is operating correctly by
asking Claude to use each one:

| Server | Quick test | Expected |
|---|---|---|
| `git` | Ask: "what files are modified?" | Lists modified files from `git status` |
| `filesystem-policy` | Ask: "list files in src/" | Returns file listing |
| `lancedb-rag` | Ask: "search codebase for X" | Returns ranked code chunks |
| `memory-graph` | Ask: "what do you remember?" | Returns memory nodes (may be empty initially) |
| `terminal` | Ask: "run the tests" | Runs configured test command or returns gated directive |
| `documentation` | Ask: "search docs for X" | Returns local doc matches (empty if no docs indexed) |

If `git` returns `fatal: not a git repository` or `lancedb-rag` returns empty
results, the most likely cause is missing or wrong `env` vars in `~/.claude.json`.
Re-check section 3b and restart Claude Code again.

---

## 6. Memory maintenance

Nightly via launchd (see `scripts/launchd.README.md`) or manually:

```bash
PY=~/.claude-env/venv/bin/python
$PY memory/memory_validator.py    --all --repair      # integrity check + safe repair
$PY memory/memory_consolidator.py --all               # merge low-value clusters
$PY memory/memory_pruner.py       --all               # dry-run report (no deletions)
$PY memory/memory_pruner.py       --namespace proj-x --apply   # actually prune
```

Pruning archives to `~/.claude-env/archive/memory/<ns>.jsonl` before deleting
and never prunes `decision` or `architecture` nodes.

### Back-filling embeddings for existing memory nodes

If nodes were written before the embedding fix was applied (i.e., they have
`embedding = NULL`), run this once to make them searchable via semantic recall:

```bash
~/.claude-env/venv/bin/python - <<'EOF'
import sys, struct
sys.path.insert(0, '/Users/YOUR_NAME/.claude-env')
import json
from lib.db import get_db
from rag.embeddings.llama_embedder import LlamaEmbedder

db = get_db()
emb = LlamaEmbedder()
rows = db.query("SELECT node_id, name, body_json FROM memory_nodes WHERE embedding IS NULL", [])
print(f"Back-filling {len(rows)} nodes...")
for r in rows:
    text = r["name"] + " " + r["body_json"]
    vec = emb.embed_documents([text])[0]
    blob = struct.pack(f"<{len(vec)}f", *vec)
    db.execute("UPDATE memory_nodes SET embedding=? WHERE node_id=?", (blob, r["node_id"]))
print("Done.")
EOF
```

Replace `YOUR_NAME` with your macOS username.

---

## 7. Approvals

```bash
~/.claude-env/bin/claude-env approvals --list-open
~/.claude-env/venv/bin/python agents/orchestration/approval_gate.py \
  --resolve <request_id> --approve --by you
~/.claude-env/venv/bin/python agents/orchestration/approval_gate.py \
  --resolve <request_id> --deny    --by you
```

Open approvals are also visible via the dashboard and the `v_open_approvals`
SQL view.

---

## 8. Observability

```bash
~/.claude-env/bin/claude-env dashboard --window 7d
~/.claude-env/bin/claude-env dashboard serve --port 8001  # Datasette (localhost only)
```

Update `PRICES` in `observability/collectors.py` to current per-1M-token
pricing for accurate cost tracking.

---

## 9. Audit verification

```bash
~/.claude-env/venv/bin/python - <<'PY'
import sys
sys.path.insert(0, "/Users/YOUR_NAME/.claude-env")
from audit.audit_logger import AuditLogger
ok, broken = AuditLogger("ops", actor="ops").verify_chain()
print("chain_ok=", ok, "first_broken=", broken)
PY
```

`False` means the ledger was tampered with or truncated. Investigate before
trusting any downstream audit queries. On a clean install this should always
return `True`.

---

## 10. Removing a repo's RAG index

```bash
# 1. Remove the LanceDB table on disk
rm -rf ~/.claude-env/knowledge/lancedb/${REPO_SLUG}__${BRANCH}.lance/

# 2. Remove bookkeeping rows from the database
~/.claude-env/venv/bin/python - <<'PYEOF'
import os, sys
sys.path.insert(0, os.path.expanduser("~/.claude-env"))
from lib.db import get_db

repo   = "your-repo-slug"
branch = "main"

db = get_db()
db.execute("DELETE FROM rag_file_state  WHERE repo=? AND branch=?", (repo, branch))
db.execute("DELETE FROM rag_index_state WHERE repo=? AND branch=?", (repo, branch))
try:
    db.commit()
except AttributeError:
    pass
print(f"Purged RAG index for {repo}@{branch}")
PYEOF
```

---

## 11. Backup & recovery

**Backup (schedule daily):**

```bash
H=~/.claude-env
sqlite3 "$H/state/claude-env.db" ".backup '$H/archive/db-$(date +%F).sqlite'"
rsync -a "$H/knowledge/lancedb/" "$H/archive/lancedb-$(date +%F)/"
# Memory JSONL archives accumulate under $H/archive/memory/ automatically.
# The venv does NOT need backup — rebuild with: python3 bootstrap.py --no-venv-create
```

**Recovery:**

```bash
H=~/.claude-env
cp  $H/archive/db-YYYY-MM-DD.sqlite     $H/state/claude-env.db
rsync -a --delete $H/archive/lancedb-YYYY-MM-DD/ $H/knowledge/lancedb/
# Recreate venv if lost:
python3 bootstrap.py --no-venv-create
$H/venv/bin/python validation/validate_installation.py
```

Recovery is complete only when `validate_installation.py` exits 0 and
`verify_chain()` returns `True`.

---

## 12. Upgrades

**Schema migration:**

```bash
PY=~/.claude-env/venv/bin/python
# Add sql/00N_description.sql, bump schema_version inside it, then:
$PY - <<'PYEOF'
import sys; sys.path.insert(0, ".")
from lib.db import get_db
get_db().apply_schema("sql/003_whatever.sql")
PYEOF
for v in installation security memory; do $PY validation/validate_$v.py; done
```

**Code + deps:**

```bash
git pull   # from the claude-env source repo
python3 bootstrap.py --no-venv-create      # update deps in existing venv
# To rebuild venv from scratch (e.g. after a Python version upgrade):
python3 bootstrap.py --recreate-venv
```

Always snapshot the database (section 11) before upgrading. On failure,
restore the snapshot.

---

## 13. Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| `validate_installation` FAILs on venv | bootstrap not yet run | `python3 bootstrap.py` |
| `validate_installation` FAILs on imports | optional model deps missing | `~/.claude-env/venv/bin/pip install <pkg>` or re-run bootstrap |
| MCP server fails to start | venv Python not in command | verify `claude mcp add` used `$H/venv/bin/python`, not bare `python` |
| `git` MCP returns `fatal: not a git repository` | `CLAUDE_ENV_REPO_ROOT` not set in `~/.claude.json` | Add env var per section 3b; restart Claude Code |
| `filesystem-policy` returns "file not found" | `CLAUDE_ENV_REPO_ROOT` not set or wrong | Add/correct env var per section 3b; restart Claude Code |
| `lancedb-rag` returns empty results | Wrong `CLAUDE_ENV_REPO_NAME` or `CLAUDE_ENV_BRANCH` | Check LanceDB filename with `ls ~/.claude-env/knowledge/lancedb/`; fix env vars; restart |
| `memory.recall` always returns `[]` | Embeddings not generated (nodes have `embedding=NULL`) | Apply the memory-graph server fix and re-write nodes, or run the back-fill script in section 6 |
| RAG returns nothing after indexing | Repo not indexed / wrong branch | `claude-env scan` then `claude-env index`; check `v_rag_health` |
| Agent "BLOCKED" on a normal file | Over-broad deny rule | Inspect `policy_violations`; adjust repo `deny`/`allow` (never global deny) |
| Reranker not improving results | ONNX model absent | Download per section 2b; `CrossEncoderReranker.ok` should be `True` |
| Audit chain reports broken | Manual edit or partial restore | Restore from a known-good backup; never edit `audit_events` directly |
| Memory recall leaks across repos | `CLAUDE_ENV_MEMORY_ISOLATED` not set for tier ≥ 2 | Set `CLAUDE_ENV_MEMORY_ISOLATED=true` in `~/.claude.json`; re-check repo policy |
| Terminal command "GATED" | State-mutating command | Route via `approval_gate.py`; only test/bench/audit run unattended |
| Progress bar not showing during index | Stderr redirected | Progress goes to stderr intentionally; JSON summary goes to stdout |

Logs: `~/.claude-env/logs/` — e.g. `incremental_index.log`, `memory.log`.
