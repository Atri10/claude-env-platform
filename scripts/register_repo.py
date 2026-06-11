#!/usr/bin/env python3
"""
register_repo.py — auto-configure MCP server env vars for a repo in ~/.claude.json.

Reads config/mcp-servers.json, resolves all ${...} placeholders for the given
repo path, then patches the project entry in ~/.claude.json so every MCP server
gets the correct CLAUDE_ENV_REPO_ROOT (and related vars) without manual editing.

Usage:
    python scripts/register_repo.py /abs/path/to/repo [--repo-name SLUG] [--branch BRANCH] [--dry-run]

    --repo-name   Short identifier for the repo (used for CLAUDE_ENV_REPO_NAME and
                  CLAUDE_ENV_MEMORY_NS). Defaults to the directory basename.
    --branch      Default branch to use for lancedb-rag (CLAUDE_ENV_BRANCH).
                  Defaults to the repo's current HEAD branch, falling back to 'main'.
    --dry-run     Print the resolved config without writing anything.

Or via the CLI dispatcher:
    claude-env register /abs/path/to/repo [--repo-name SLUG] [--branch BRANCH]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parents[1]   # root of the claude-env source tree
_HOME = Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))
_CLAUDE_JSON = Path.home() / ".claude.json"
_MCP_CONFIG = _HERE / "config" / "mcp-servers.json"

GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"
RESET  = "\033[0m"


def _detect_branch(repo_root: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", repo_root, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        return out or "main"
    except Exception:
        return "main"


def _resolve(value: str, subs: dict[str, str]) -> str:
    for k, v in subs.items():
        value = value.replace(f"${{{k}}}", v)
    return value


def _resolve_env(env: dict, subs: dict[str, str]) -> dict:
    return {k: _resolve(v, subs) for k, v in env.items()}


def _build_server_blocks(repo_root: str, repo_name: str, branch: str) -> dict:
    """Return a dict of server_name -> full MCP server block with resolved env."""
    cfg = json.loads(_MCP_CONFIG.read_text())
    defaults_env: dict = cfg.get("defaults", {}).get("env", {})

    subs = {
        "HOME":                  str(Path.home()),
        "CLAUDE_ENV_HOME":       str(_HOME),
        "workspaceFolder":       repo_root,
        "CLAUDE_ENV_REPO_ROOT":  repo_root,
        "CLAUDE_ENV_REPO_NAME":  repo_name,
        "CLAUDE_ENV_BRANCH":     branch,
    }

    result = {}
    for name, srv in cfg.get("mcpServers", {}).items():
        if name == "jetbrains" or srv.get("optional"):
            continue

        # resolve command + args
        command = _resolve(srv["command"], subs)
        args    = [_resolve(a, subs) for a in srv.get("args", [])]

        # merge defaults env + server-specific env, resolve all
        merged_env = {**defaults_env, **srv.get("env", {})}
        resolved_env = _resolve_env(merged_env, subs)

        # server-specific extra vars not in mcp-servers.json
        if name == "lancedb-rag":
            resolved_env.setdefault("CLAUDE_ENV_REPO_NAME", repo_name)
            resolved_env.setdefault("CLAUDE_ENV_BRANCH",    branch)
        if name == "memory-graph":
            resolved_env.setdefault("CLAUDE_ENV_MEMORY_NS",       f"proj-{repo_name}")
            resolved_env.setdefault("CLAUDE_ENV_MEMORY_ISOLATED",  "false")
        if name == "documentation":
            resolved_env.setdefault("CLAUDE_ENV_DOCS_DIR",
                                    str(_HOME / "knowledge" / "docs"))

        result[name] = {
            "type":    "stdio",
            "command": command,
            "args":    args,
            "env":     resolved_env,
        }
    return result


def _patch_claude_json(repo_root: str, server_blocks: dict, dry_run: bool) -> None:
    if not _CLAUDE_JSON.exists():
        print(f"{YELLOW}WARN{RESET} ~/.claude.json not found — "
              "register MCP servers with `claude mcp add` first, then re-run this script.")
        sys.exit(1)

    data = json.loads(_CLAUDE_JSON.read_text())
    projects: dict = data.setdefault("projects", {})
    project: dict  = projects.setdefault(repo_root, {})
    existing: dict = project.setdefault("mcpServers", {})

    changed = []
    for name, block in server_blocks.items():
        prior_env = existing.get(name, {}).get("env", {})
        new_env   = block["env"]
        if prior_env != new_env:
            changed.append(name)
        if name in existing:
            # preserve Claude Code fields (type, command, args) but overwrite env
            existing[name]["env"] = new_env
        else:
            existing[name] = block

    if dry_run:
        print(f"\n{YELLOW}DRY RUN — nothing written{RESET}\n")
        print("Resolved server blocks:")
        print(json.dumps(server_blocks, indent=2))
        return

    if not changed:
        print(f"{GREEN}Already up-to-date{RESET} — no changes needed for {repo_root}")
        return

    _CLAUDE_JSON.write_text(json.dumps(data, indent=2))
    print(f"{GREEN}Patched ~/.claude.json{RESET} for project: {repo_root}")
    print(f"  Updated env vars for: {', '.join(changed)}")
    print(f"\n{YELLOW}ACTION REQUIRED:{RESET} Restart Claude Code so MCP servers pick up the new env.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Auto-configure MCP server env vars for a repo in ~/.claude.json")
    parser.add_argument("repo_root", help="Absolute path to the repository root")
    parser.add_argument("--repo-name", default=None,
                        help="Short repo identifier (defaults to directory basename)")
    parser.add_argument("--branch", default=None,
                        help="Default branch for lancedb-rag (auto-detected if omitted)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print resolved config without writing")
    args = parser.parse_args()

    repo_root = str(Path(args.repo_root).resolve())
    if not Path(repo_root).is_dir():
        print(f"{RED}ERROR{RESET} repo path does not exist: {repo_root}")
        return 1

    repo_name = args.repo_name or Path(repo_root).name
    branch    = args.branch    or _detect_branch(repo_root)

    print(f"Repo root : {repo_root}")
    print(f"Repo name : {repo_name}")
    print(f"Branch    : {branch}")
    print(f"HOME      : {_HOME}")

    blocks = _build_server_blocks(repo_root, repo_name, branch)
    _patch_claude_json(repo_root, blocks, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
