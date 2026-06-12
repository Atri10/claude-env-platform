---
description: Generate a claude-env compliance evidence report (audit ledger, violations, approvals, costs, chain verification)
argument-hint: "[window e.g. 7d|30d] [repo]"
allowed-tools: Bash
---

Generate and summarize a compliance report from the local claude-env audit ledger.

1. Parse `$ARGUMENTS`: first token that looks like `<N>d|<N>h|<N>w` is the
   window (default `30d`); any other token is the repo filter.
2. Run:
   ```
   ${CLAUDE_ENV_HOME:-$HOME/.claude-env}/bin/claude-env report --window <window> [--repo <repo>]
   ```
3. Present the report. Lead with the ledger-integrity line — if the chain is
   NOT verified, flag that prominently as the single most important finding.
