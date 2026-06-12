---
description: Replay a past session step-by-step from the claude-env audit ledger (forensics)
argument-hint: "[session_id]"
allowed-tools: Bash
---

Reconstruct what happened in a session from the tamper-evident ledger.

1. If `$ARGUMENTS` is empty, run
   `${CLAUDE_ENV_HOME:-$HOME/.claude-env}/bin/claude-env replay --list`
   and show the recent sessions so the user can pick one.
2. Otherwise run
   `${CLAUDE_ENV_HOME:-$HOME/.claude-env}/bin/claude-env replay $ARGUMENTS`
   and walk the user through the timeline, calling out policy blocks,
   security events, and approval gates.
