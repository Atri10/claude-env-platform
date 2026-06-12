#!/usr/bin/env bash
# Nightly maintenance: ingest transcripts → validate → consolidate → prune (dry-run)
# → per-repo analyst digests.
# Pruning is a dry-run by default; add --apply flag to the pruner call after reviewing logs.
# Scheduled via launchd (scripts/launchd.README.md) on macOS,
# or systemd user units (scripts/systemd/) on Linux.
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

# 1. ingest new Claude Code session transcripts into the memory graph
#    (also records retrieval->edit usage signals for the feedback loop)
"$PY" "${H}/memory/session_ingestor.py"                   >> "$LOG" 2>&1 || true

# 2. memory maintenance
"$PY" "${H}/memory/memory_validator.py"    --all --repair >> "$LOG" 2>&1
"$PY" "${H}/memory/memory_consolidator.py" --all          >> "$LOG" 2>&1
"$PY" "${H}/memory/memory_pruner.py"       --all          >> "$LOG" 2>&1

# 3. per-repo analyst digests — one repo path per line in
#    $CLAUDE_ENV_HOME/config/analyst-repos.txt (comments with '#')
REPOS="${H}/config/analyst-repos.txt"
if [[ -f "$REPOS" ]]; then
  grep -v '^\s*#' "$REPOS" | while read -r repo; do
    [[ -z "$repo" ]] && continue
    "$PY" "${H}/agents/analysts/nightly_analyst.py" "$repo" >> "$LOG" 2>&1 || true
  done
fi

echo "nightly complete: $(date)"                          >> "$LOG"
