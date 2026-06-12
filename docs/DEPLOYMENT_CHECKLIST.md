# claude-env — Deployment Checklist

Clean macOS Apple Silicon machine → all validators green. Work top to bottom;
each section depends on the one above it. Refer to `docs/RUNBOOK.md` for the
full command details behind each step.

---

## Pre-flight (host machine)

- [ ] macOS Apple Silicon — `uname -m` prints `arm64`
- [ ] FileVault enabled (encrypts the local audit store, model weights, and DB)
- [ ] Xcode Command Line Tools installed — `xcode-select -p` returns a path
- [ ] Homebrew installed — `brew --version` returns a version
- [ ] Python ≥ 3.13 — `python3 --version` prints `Python 3.13.x` or higher
      _(install via `brew install python@3.13` if older)_
- [ ] Git ≥ 2.40 — `git --version`
- [ ] Claude Code installed and authenticated — `claude --version`

---

## Platform install

- [ ] Clone or copy the `claude-env` repo to a working directory
- [ ] **Run bootstrap:**
      ```bash
      python3 bootstrap.py --with-brew
      ```
      This creates `~/.claude-env/venv/`, installs all Python deps, initialises
      the SQLite DB, copies policy files, and writes the genesis audit event.
- [ ] Lock down the directory:
      ```bash
      chmod 700 ~/.claude-env
      ```
- [ ] Add to `~/.zshrc` (then open a new terminal or `source ~/.zshrc`):
      ```zsh
      export CLAUDE_ENV_HOME="$HOME/.claude-env"
      export PATH="$CLAUDE_ENV_HOME/bin:$PATH"
      ```
- [ ] Confirm venv Python version:
      ```bash
      ~/.claude-env/venv/bin/python --version   # must be 3.13+
      ```
- [ ] First validation (model warnings are OK at this stage):
      ```bash
      claude-env validate installation
      ```

---

## Local models

> No model ships with the code — these are **required setup steps**. Model names
> below are suggestions; substitute any GGUF embedding model / ONNX cross-encoder.
> No symlinks anywhere — point `config/rag.yaml` straight at the downloaded files.

- [ ] Download a GGUF embedding model into `~/.claude-env/models/` (RUNBOOK §2a):
      ```bash
      huggingface-cli download nomic-ai/nomic-embed-text-v1.5-GGUF \
        nomic-embed-text-v1.5.Q8_0.gguf \
        --local-dir ~/.claude-env/models
      ```
- [ ] **Configure it in `config/rag.yaml`** (required — no default):
      `embedding.model_path`, matching `embedding_dim`, and `document_prefix` /
      `query_prefix` if the model needs them (e.g. nomic). Or set `EMBED_MODEL_PATH`
      / `EMBED_DIM` / `EMBED_DOC_PREFIX` / `EMBED_QUERY_PREFIX` (RUNBOOK §2c).
- [ ] Install llama-cpp-python with Metal into the venv:
      ```bash
      CMAKE_ARGS="-DLLAMA_METAL=on" \
        ~/.claude-env/venv/bin/pip install --upgrade --force-reinstall llama-cpp-python
      ```
- [ ] (Optional) Download a reranker ONNX model + set `reranker.model_dir` (RUNBOOK §2b):
      ```bash
      huggingface-cli download cross-encoder/ms-marco-MiniLM-L-6-v2 \
        --include "*.onnx" "*.json" "*.txt" \
        --local-dir ~/.claude-env/models/reranker-onnx/
      # then in config/rag.yaml: reranker.model_dir: "~/.claude-env/models/reranker-onnx"
      ```
- [ ] Validate models load:
      ```bash
      claude-env validate installation
      # "embedding model file present" must show PASS once configured.
      # "reranker loads + runs" shows PASS if enabled, WARN if disabled/absent.
      # A WARN's next line explains why; RAG still works without the reranker.
      ```

---

## Security framework

- [ ] `claude-env validate security` — exits 0
- [ ] Confirm audit ledger is append-only:
      Open `~/.claude-env/state/claude-env.db` in any SQLite client and run:
      ```sql
      UPDATE audit_events SET event_type='tamper' WHERE 1=1;
      ```
      This must be rejected by the DB trigger.
- [ ] Review `~/.claude-env/config/global-policy.yaml` deny lists against
      your organisation's data-handling requirements

---

## MCP layer

- [ ] Ensure the `mcp` package is present in the venv:
      ```bash
      ~/.claude-env/venv/bin/pip install mcp
      ```
      _(bootstrap installs it; re-run if you see import errors)_
- [ ] Register all six servers globally (RUNBOOK §3a):
      ```bash
      H=~/.claude-env; PY="$H/venv/bin/python"
      claude mcp add filesystem-policy -- "$PY" "$H/mcp-servers/filesystem-policy/server.py"
      claude mcp add git               -- "$PY" "$H/mcp-servers/git/server.py"
      claude mcp add lancedb-rag       -- "$PY" "$H/mcp-servers/lancedb-rag/server.py"
      claude mcp add memory-graph      -- "$PY" "$H/mcp-servers/memory-graph/server.py"
      claude mcp add terminal          -- "$PY" "$H/mcp-servers/terminal/server.py"
      claude mcp add documentation     -- "$PY" "$H/mcp-servers/documentation/server.py"
      ```
- [ ] `claude mcp list` — all six servers appear
- [ ] **Auto-configure per-project env vars** (RUNBOOK §3b) —
      this is the step most commonly missed. Run once per repo:
      ```bash
      claude-env register /absolute/path/to/repo
      ```
      This patches `~/.claude.json` with the correct `CLAUDE_ENV_REPO_ROOT`,
      `CLAUDE_ENV_REPO_NAME`, `CLAUDE_ENV_BRANCH`, and memory namespace for
      every server. Use `--dry-run` to preview first.
- [ ] **Restart Claude Code** so servers pick up the new environment
- [ ] `claude-env validate mcp` — exits 0

---

## Native-tool enforcement (hooks)

- [ ] Install the Claude Code hooks so policy + audit cover native tools
      (Read/Write/Edit/Bash), not just MCP traffic (RUNBOOK §14a):
      ```bash
      claude-env hooks
      ```
- [ ] Restart Claude Code, then confirm enforcement: ask Claude to read a
      blocked file (e.g. `.env`) — the tool call must be denied with the
      matched policy rule
- [ ] Tier-2+ machines: set `CLAUDE_ENV_HOOK_FAIL_CLOSED=true` in the
      environment Claude Code runs under

---

## Agents

- [ ] `claude-env validate agents` — exits 0
- [ ] Spot-check routing on 2–3 real tasks:
      ```bash
      claude-env route "design a new caching layer" --all
      claude-env route "find the SQL injection bug" --all
      ```

---

## First repository

- [ ] Create `.claude/repo-policy.yaml` — set `tier`, `repo` slug,
      allow/deny/rag scope (RUNBOOK §4a)
- [ ] Preview the policy split (no model needed):
      ```bash
      claude-env scan /path/to/repo
      ```
      Confirm the blocked list contains only secrets/generated files
- [ ] Index the repository:
      ```bash
      claude-env index /path/to/repo
      ```
      Watch the progress bar; note the final chunk count
- [ ] Validate RAG:
      ```bash
      claude-env validate rag /path/to/repo "test query relevant to the codebase"
      ```
      Must return ranked results (not empty)
- [ ] Install the post-commit hook:
      ```bash
      ln -sf ~/.claude-env/scripts/post-commit /path/to/repo/.git/hooks/post-commit
      chmod +x /path/to/repo/.git/hooks/post-commit
      ```
- [ ] Add project-specific MCP env vars to `~/.claude.json` for this repo
      (RUNBOOK §3b) and restart Claude Code
- [ ] Verify all MCPs work for the repo (RUNBOOK §5)

---

## Memory & observability

- [ ] `claude-env validate memory` — exits 0
- [ ] Install the nightly launchd job — follow `scripts/launchd.README.md`
- [ ] `claude-env dashboard` — renders without error; latency rows populate
      after the first retrieval

---

## Hardening

- [ ] Full suite green:
      ```bash
      claude-env validate all
      ```
- [ ] Threat → control mapping in `docs/IMPLEMENTATION.md` reviewed
- [ ] Backup job scheduled (RUNBOOK §11); restore drill performed at least once
- [ ] Audit chain verifies clean after restore:
      ```bash
      ~/.claude-env/venv/bin/python - <<'PY'
      import sys; sys.path.insert(0, "/Users/YOUR_NAME/.claude-env")
      from audit.audit_logger import AuditLogger
      ok, broken = AuditLogger("ops", actor="ops").verify_chain()
      print("chain_ok=", ok, "first_broken=", broken)
      PY
      ```

---

## Operational readiness

- [ ] RUNBOOK distributed to all operators
- [ ] Upgrade procedure rehearsed on a DB snapshot (RUNBOOK §12)
- [ ] Incident drill performed once: `claude-env incident on --reason drill`,
      confirm everything is denied, `claude-env incident off` (RUNBOOK §14b)
- [ ] Nightly job scheduled — launchd (macOS, RUNBOOK §6) or systemd timer
      (Linux, RUNBOOK §14i); repos listed in `~/.claude-env/config/analyst-repos.txt`
- [ ] Cost budgets set per repo in `~/.claude-env/config/budgets.yaml` (RUNBOOK §14g)
- [ ] `claude-env validate features` — exits 0 (extended feature smoke suite)
- [ ] PostgreSQL migration plan reviewed if multi-machine deployment is planned
      (`docs/POSTGRES_MIGRATION.md`)

---

Deployment is complete when every box is ticked and `claude-env validate all`
exits 0.
