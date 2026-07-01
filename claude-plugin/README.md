# claude-env — Claude Code plugin

Packages the claude-env governance layer for one-step distribution to a team:
policy + audit hooks, the local MCP servers, and slash commands
(`/claude-env:know`, `/claude-env:report`, `/claude-env:replay`).

## Prerequisite

The plugin is a thin wiring layer — the platform itself must be bootstrapped on
the machine first:

```bash
python3 bootstrap.py          # creates ~/.claude-env (venv, DB, policies, code mirror)
```

See the main `README.md` §10 "Local models" for the (required) embedding-model setup.

## Install

From a marketplace that includes this repo:

```
/plugin marketplace add <org>/claude-env
/plugin install claude-env
```

Or for local testing:

```bash
claude --plugin-dir /path/to/claude-env/claude-plugin
```

## What it wires up

| Piece | Effect |
|---|---|
| `hooks/hooks.json` | PreToolUse policy enforcement + PostToolUse audit for Claude Code's native tools |
| `.mcp.json` | filesystem-policy, git, lancedb-rag, memory-graph servers |
| `commands/` | `/claude-env:know`, `/claude-env:report`, `/claude-env:replay` |

Alternative without the plugin system: `claude-env hooks` installs the same
hooks into `~/.claude/settings.json`, and RUNBOOK §3 registers the MCP servers
via `claude mcp add`.
