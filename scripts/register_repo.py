#!/usr/bin/env python3
"""
register_repo.py — onboard a repo to claude-env: MCP env vars + governance template.

Two things happen when you register a repo:
  1. Reads config/mcp-servers.json, resolves all ${...} placeholders for the given
     repo path, then patches the project entry in ~/.claude.json so every MCP
     server gets the correct CLAUDE_ENV_REPO_ROOT (and related vars).
  2. Installs the onboarding template from templates/repo-onboarding/ into the repo:
       - CLAUDE.md   -> the MCP-first governance operating contract (managed block;
                        local edits outside the block are preserved and re-runs
                        only refresh the block).
       - .claude/skills/  -> principled-engineering, solid-design, design-patterns,
                             code-review, architecture-review
       - .claude/agents/  -> code-reviewer, architecture-reviewer subagents

Usage:
    python scripts/register_repo.py /abs/path/to/repo [--repo-name SLUG] [--branch BRANCH]
                                    [--no-template] [--force-template] [--dry-run]

    --repo-name       Short identifier for the repo (used for CLAUDE_ENV_REPO_NAME and
                      CLAUDE_ENV_MEMORY_NS). Defaults to the directory basename.
    --branch          Default branch to use for lancedb-rag (CLAUDE_ENV_BRANCH).
                      Defaults to the repo's current HEAD branch, falling back to 'main'.
    --no-template     Only patch ~/.claude.json; skip the CLAUDE.md + .claude/ install.
    --force-template  Overwrite existing .claude/ skill & agent files (CLAUDE.md's
                      managed block is always refreshed regardless).
    --dry-run         Print what would change without writing anything.

Or via the CLI dispatcher:
    claude-env register /abs/path/to/repo [--repo-name SLUG] [--branch BRANCH]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parents[1]   # root of the claude-env source tree
_HOME = Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))
_CLAUDE_JSON = Path.home() / ".claude.json"
_MCP_CONFIG = _HERE / "config" / "mcp-servers.json"
_TEMPLATE_DIR = _HERE / "templates" / "repo-onboarding"

# Managed-block markers in the target repo's CLAUDE.md. Everything between these
# is owned by the platform and replaced on each register; text outside is kept.
_BLOCK_BEGIN = "<!-- CLAUDE-ENV:BEGIN (managed)"
_BLOCK_END   = "<!-- CLAUDE-ENV:END -->"

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


def _detect_tier(repo_root: str) -> str:
    """Read tier from <repo>/.claude/repo-policy.yaml if present; default '1'."""
    policy = Path(repo_root) / ".claude" / "repo-policy.yaml"
    if policy.exists():
        for line in policy.read_text().splitlines():
            m = re.match(r"\s*tier\s*:\s*([0-3])\b", line)
            if m:
                return m.group(1)
    return "1"


def _fill(text: str, repo_name: str, tier: str) -> str:
    return text.replace("{{REPO_NAME}}", repo_name).replace("{{TIER}}", tier)


def _install_claude_md(repo_root: str, repo_name: str, tier: str,
                       dry_run: bool) -> str:
    """Install/refresh the managed CLAUDE.md block, preserving any local text.

    Returns a short status word for logging.
    """
    src = _TEMPLATE_DIR / "CLAUDE.md"
    if not src.exists():
        return "template-missing"
    managed = _fill(src.read_text(), repo_name, tier)

    dst = Path(repo_root) / "CLAUDE.md"
    if not dst.exists():
        if not dry_run:
            dst.write_text(managed)
        return "created"

    current = dst.read_text()
    if _BLOCK_BEGIN not in current or _BLOCK_END not in current:
        # Existing CLAUDE.md with no managed block — prepend ours, keep theirs.
        merged = managed.rstrip() + "\n\n" + current.lstrip()
        if not dry_run:
            dst.write_text(merged)
        return "block-prepended"

    # Replace only the managed span; keep everything before/after it verbatim.
    # The template's own trailing (unmanaged) footer is dropped in this path so
    # we don't duplicate the user's out-of-block content.
    new_block = managed[managed.index(_BLOCK_BEGIN):
                        managed.index(_BLOCK_END) + len(_BLOCK_END)]
    pattern = re.compile(re.escape(_BLOCK_BEGIN) + r".*?" + re.escape(_BLOCK_END),
                         re.DOTALL)
    merged = pattern.sub(lambda _: new_block, current, count=1)
    if merged == current:
        return "up-to-date"
    if not dry_run:
        dst.write_text(merged)
    return "block-updated"


def _install_dot_claude(repo_root: str, force: bool, dry_run: bool) -> list[str]:
    """Copy skills/ and agents/ from the template into <repo>/.claude/.

    Idempotent: existing files are left in place unless --force-template. Returns
    the list of relative paths written (or that would be written on dry-run).
    """
    written: list[str] = []
    src_root = _TEMPLATE_DIR / ".claude"
    if not src_root.exists():
        return written
    for src in src_root.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(_TEMPLATE_DIR)          # e.g. .claude/skills/.../SKILL.md
        dst = Path(repo_root) / rel
        if dst.exists() and not force:
            continue
        written.append(str(rel))
        if not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    return written


def _install_template(repo_root: str, repo_name: str, force: bool,
                      dry_run: bool) -> None:
    if not _TEMPLATE_DIR.exists():
        print(f"{YELLOW}WARN{RESET} onboarding template not found at "
              f"{_TEMPLATE_DIR} — skipping CLAUDE.md / skills install.")
        return

    tier = _detect_tier(repo_root)
    tag = f"{YELLOW}DRY RUN{RESET} " if dry_run else ""

    md_status = _install_claude_md(repo_root, repo_name, tier, dry_run)
    print(f"{tag}CLAUDE.md (tier {tier}): {md_status}")

    files = _install_dot_claude(repo_root, force, dry_run)
    if files:
        print(f"{tag}{GREEN}Installed{RESET} {len(files)} skill/agent file(s) "
              f"under .claude/:")
        for f in files:
            print(f"    {f}")
    else:
        print(f"{tag}.claude/ skills & agents already present "
              f"(use --force-template to overwrite)")


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
    parser.add_argument("--no-template", action="store_true",
                        help="Skip installing CLAUDE.md + .claude/ skills & agents")
    parser.add_argument("--force-template", action="store_true",
                        help="Overwrite existing .claude/ skill & agent files")
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

    if not args.no_template:
        print()
        _install_template(repo_root, repo_name, args.force_template, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
