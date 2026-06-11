#!/usr/bin/env zsh
# Nightly memory maintenance: validate → consolidate → prune (dry-run).
# Pruning is a dry-run by default; add --apply flag to the pruner call after reviewing logs.
# Scheduled via launchd — see scripts/launchd.README.md.
set -e
H="${CLAUDE_ENV_HOME:-$HOME/.claude-env}"
PY="${H}/venv/bin/python"

if [[ ! -x "$PY" ]]; then
  echo "[claude-env] venv not found at $PY — run bootstrap.py first" >&2
  exit 1
fi

mkdir -p "${H}/logs"
LOG="${H}/logs/memory.log"

echo "=== nightly memory run: $(date) ===" >> "$LOG"
"$PY" "${H}/memory/memory_validator.py"    --all --repair >> "$LOG" 2>&1
"$PY" "${H}/memory/memory_consolidator.py" --all          >> "$LOG" 2>&1
"$PY" "${H}/memory/memory_pruner.py"       --all          >> "$LOG" 2>&1
echo "nightly complete: $(date)"                          >> "$LOG"
