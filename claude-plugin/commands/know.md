---
description: Ask claude-env what it knows about a topic — fused memory graph + RAG index + git history with provenance
argument-hint: <topic>
allowed-tools: Bash
---

Run the claude-env unified recall for the user's topic and present the results.

1. Determine the repo name from the current directory (`basename $PWD`).
2. Run:
   ```
   ${CLAUDE_ENV_HOME:-$HOME/.claude-env}/bin/claude-env know "$ARGUMENTS" --repo <repo-name> --repo-root "$PWD"
   ```
3. Present the findings grouped by source (memory / code index / git), keeping
   the provenance visible. If a section is empty or degraded, say so briefly —
   do not pad with speculation.
