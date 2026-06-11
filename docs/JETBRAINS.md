# claude-env — JetBrains Integration

Target IDEs: IntelliJ IDEA Ultimate, PyCharm Professional, WebStorm, GoLand, Rider,
CLion. All use the same plugin + MCP wiring; settings paths are identical across the
suite.

## Plugins (install in this order)
1. **Claude Code** (JetBrains Marketplace) — the in-IDE agent surface.
2. **MCP Host / MCP Server bridge** for JetBrains — exposes editor context,
   inspections, refactor actions, and run configurations as MCP tools so Claude Code
   can read IDE state. (Bundled with recent Claude Code plugin builds; install
   separately only if your build doesn't include it.)
No other plugins are required. Do **not** install third-party "AI" plugins that phone
home — this platform is local-only.

## Installation sequence
1. Install the platform and register the six MCP servers (RUNBOOK §1–§2).
2. In the IDE: `Settings → Plugins → Marketplace` → install **Claude Code** → restart.
3. `Settings → Tools → Claude Code`:
   - Point it at your `claude` CLI (it usually auto-detects).
   - Enable "Use project MCP configuration" and select this repo's
     `config/mcp-servers.json` (or the `$CLAUDE_ENV_HOME` copy).
   - Set environment for the project (Run/Debug template env or IDE-level):
     `CLAUDE_ENV_REPO_ROOT=$ProjectFileDir$`, `CLAUDE_ENV_TIER`,
     `CLAUDE_ENV_MEMORY_NS=proj-<slug>`, `CLAUDE_ENV_MEMORY_ISOLATED`.
4. Confirm the `jetbrains` server appears alongside `filesystem-policy` etc. in the
   Claude Code MCP panel.

## IDE settings (recommended)
- `Settings → Tools → Actions on Save`: enable "Reformat" and "Optimize imports" so
  agent-authored diffs match house style before review.
- `Editor → Inspections`: keep the language inspection profile strict; the
  Architecture/Performance/Security agents consume inspection output via the bridge.
- `Version Control → Commit`: enable "Run Git hooks" so the `post-commit` incremental
  indexer fires from IDE commits too.
- `Settings → Tools → Terminal`: leave the shell as zsh; the `terminal` MCP server,
  not the IDE terminal, is what agents use (allow-listed commands only).
- Index hygiene: mark `generated/`, `vendor/`, `node_modules/` as **Excluded** so IDE
  navigation matches what the RAG `exclude_paths` already skip.

## Workflows

### Code review
1. Open the diff (`Git → Show Diff` or the Commit panel).
2. In Claude Code, run a review on the changeset. The orchestrator routes to the
   **Security** and **Performance** agents (read-only) plus **Testing** for coverage.
3. Findings come back referenced to file:line via the `jetbrains` bridge; jump to each
   with one click. Security findings are also written to `security_events`.
4. Apply fixes through the relevant write-capable agent (Backend/Frontend); every edit
   still passes `filesystem-policy`.

### Refactoring
1. Select the symbol/scope; ask Claude Code to refactor (e.g. "extract service,
   preserve behavior").
2. The **Backend/Frontend** agent proposes a diff confined to its `write_paths`.
   Prefer IDE-native refactors (Rename, Extract Method) for mechanical steps — the
   agent coordinates, the IDE executes safe transforms.
3. Run `terminal.run_tests` (via the agent) before accepting.
4. The **Architect** records any structural decision as an ADR (Documentation agent
   commits it under `docs/ADRs/**`).

### Architecture review
1. Ask the **Architect** agent for a review of a module or proposed change.
2. It reads source (read-only), pulls related `decision`/`architecture` memory, and
   RAG context, then produces boundaries, coupling notes, and trade-offs.
3. Disagreements with implementers are reconciled by `conflict_resolver` (architect
   precedence on design trade-offs; security veto on vulnerabilities).
4. Output is an ADR/RFC, not code.

### Debugging
1. Reproduce under the IDE debugger; capture the failing test or stack.
2. Give Claude Code the failure context; the **Backend/Testing** agents use
   `git.blame`/`git.log` and RAG to localize, propose a fix, and add a regression test
   in `tests/**`.
3. `terminal.run_tests` confirms green before the fix is accepted.
4. The investigation is stored as an `investigation` memory node, linked to the
   affected entities, so a recurrence is recognized.

## Security note
The IDE bridge respects the same `filesystem-policy` chokepoint: a file blocked by the
repo policy is not surfaced to Claude Code even if it is open in the editor. There is
no separate IDE credential store and no outbound calls beyond the tier-gated
documentation fetch.
