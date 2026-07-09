#!/usr/bin/env python3
"""
register_repo.py — onboard a repo to claude-env in one smooth, interactive step.

Run it once per repo. Interactively (a TTY, no --yes) it asks for a few details
with sensible detected defaults, then provisions the repo's ISOLATED workspace:

  1. Repo policy   -> writes <repo>/.claude/repo-policy.yaml from the template with
                      the real slug, tier, and memory namespace filled in (memory is
                      isolated automatically for tier>=2). This file IS the isolation
                      boundary the policy engine + hooks enforce.
  2. Namespaces    -> RAG index table  <slug>__<branch>  (under ~/.claude-env/knowledge/
                      lancedb) and memory namespace  proj-<slug>.
  3. MCP env       -> patches ~/.claude.json so every MCP server for this project
                      resolves to the correct repo root, slug, branch, and namespaces.
  4. Template      -> installs CLAUDE.md (governance contract, with the concrete
                      namespace values — no placeholders left) plus .claude/skills/
                      (principled-engineering, solid-design, design-patterns,
                      code-review, architecture-review) and .claude/agents/
                      (code-reviewer, architecture-reviewer).
  5. Index (opt-in)-> offers to build the first RAG index now.

Usage:
    claude-env onboard /abs/path/to/repo            # interactive (recommended)
    claude-env register /abs/path/to/repo --yes     # non-interactive, accept defaults
    python scripts/register_repo.py /abs/path/to/repo [flags]

    --repo-name SLUG   Repo slug for RAG + memory namespaces (default: dir name).
    --tier {0,1,2,3}   Privacy tier (prompted if omitted; existing policy is the default).
    --description STR  Short human-readable purpose.
    --branch BRANCH    Default branch for lancedb-rag (auto-detected if omitted).
    --yes, -y          Non-interactive: accept detected defaults, no prompts.
    --no-template      Skip CLAUDE.md + .claude/ install (namespaces/env only).
    --force-template   Overwrite existing .claude/ skill & agent files.
    --force-policy     Regenerate an existing .claude/repo-policy.yaml.
    --dry-run          Print what would change without writing anything.

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
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from lib.logging_setup import get_logger  # noqa: E402

_log = get_logger("onboarding")
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


def _build_server_blocks(repo_root: str, repo_name: str, branch: str,
                         tier: int = 1) -> dict:
    """Return a dict of server_name -> full MCP server block with resolved env.
    Memory is isolated (no cross-project reads) for tier>=2."""
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
            resolved_env["CLAUDE_ENV_MEMORY_NS"]       = f"proj-{repo_name}"
            resolved_env["CLAUDE_ENV_MEMORY_ISOLATED"] = "true" if tier >= 2 else "false"
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


def _fill(text: str, subs: dict[str, str]) -> str:
    for key, val in subs.items():
        text = text.replace("{{" + key + "}}", val)
    return text


def _install_claude_md(repo_root: str, subs: dict[str, str], dry_run: bool) -> str:
    """Install/refresh the managed CLAUDE.md block, preserving any local text.

    Returns a short status word for logging.
    """
    src = _TEMPLATE_DIR / "CLAUDE.md"
    if not src.exists():
        return "template-missing"
    managed = _fill(src.read_text(), subs)

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
        # never leak OS/tooling junk into an onboarded repo
        if src.name == ".DS_Store" or "__pycache__" in src.parts \
                or src.suffix == ".pyc":
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


def _install_template(repo_root: str, subs: dict[str, str], force: bool,
                      dry_run: bool) -> None:
    if not _TEMPLATE_DIR.exists():
        print(f"{YELLOW}WARN{RESET} onboarding template not found at "
              f"{_TEMPLATE_DIR} — skipping CLAUDE.md / skills install.")
        return

    tag = f"{YELLOW}DRY RUN{RESET} " if dry_run else ""

    md_status = _install_claude_md(repo_root, subs, dry_run)
    print(f"{tag}CLAUDE.md: {md_status}")

    files = _install_dot_claude(repo_root, force, dry_run)
    if files:
        print(f"{tag}{GREEN}Installed{RESET} {len(files)} skill/agent file(s) "
              f"under .claude/:")
        for f in files:
            print(f"    {f}")
    else:
        print(f"{tag}.claude/ skills & agents already present "
              f"(use --force-template to overwrite)")


# ---------------------------------------------------------------------------
# interactive onboarding: gather details, provision the repo's isolated space
# ---------------------------------------------------------------------------
def _slugify(name: str) -> str:
    """A stable, filesystem/namespace-safe repo slug (lowercase [a-z0-9._-])."""
    s = re.sub(r"[^a-z0-9._-]+", "-", name.strip().lower())
    return re.sub(r"-{2,}", "-", s).strip("-.") or "repo"


def _table_name(slug: str, branch: str) -> str:
    """Mirror rag/retrievers/lance_store.table_name so we can report/pre-create."""
    safe = lambda s: s.replace("/", "-").replace(" ", "_")
    return f"{safe(slug)}__{safe(branch)}"


def _interactive(no_prompt: bool) -> bool:
    return (not no_prompt) and sys.stdin.isatty() and sys.stdout.isatty()


def _prompt(label: str, default: str) -> str:
    try:
        resp = input(f"  {label} [{default}]: ").strip()
    except EOFError:
        return default
    return resp or default


def _prompt_tier(default: str) -> str:
    while True:
        v = _prompt("Privacy tier — 0 public / 1 internal / 2 sensitive / 3 restricted",
                    default)
        if v in ("0", "1", "2", "3"):
            return v
        print(f"    {YELLOW}enter 0, 1, 2, or 3{RESET}")


def _write_repo_policy(repo_root: str, slug: str, tier: str, description: str,
                       force: bool, dry_run: bool) -> str:
    """Create <repo>/.claude/repo-policy.yaml — the repo's isolation boundary —
    from the template with all placeholders filled. Returns a status word.
    Never clobbers an existing policy unless force=True."""
    dst = Path(repo_root) / ".claude" / "repo-policy.yaml"
    tmpl = _HERE / "config" / "repo-policy.template.yaml"
    if not tmpl.exists():
        return "template-missing"
    existed = dst.exists()
    if existed and not force:
        return "kept-existing (use --force-policy to regenerate)"

    text = tmpl.read_text()
    text = text.replace("EXAMPLE-REPO-SLUG", slug)            # repo: + proj- namespace
    text = re.sub(r"(?m)^tier:\s*\d+", f"tier: {tier}", text, count=1)
    if description:
        safe_desc = description.replace('"', "'")
        text = re.sub(r'(?m)^description:\s*".*"',
                      f'description: "{safe_desc}"', text, count=1)
    if tier in ("2", "3"):                                    # sensitive+ -> isolate memory
        text = re.sub(r"(?m)^(\s*isolated:\s*)false", r"\g<1>true", text, count=1)

    if not dry_run:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text)
    return "overwritten" if existed else "created"


def _provision_storage(slug: str, branch: str, dry_run: bool) -> str:
    """Ensure the isolated RAG store exists and return this repo's table name.
    The memory namespace is created lazily on first write, so nothing to do
    there beyond reporting it."""
    lancedb_dir = _HOME / "knowledge" / "lancedb"
    if not dry_run:
        lancedb_dir.mkdir(parents=True, exist_ok=True)
    return _table_name(slug, branch)


def _install_post_commit(repo_root: str, dry_run: bool) -> str:
    """Install scripts/post-commit into <repo>/.git/hooks so each commit triggers
    an incremental RAG re-index. Idempotent; never clobbers a foreign hook."""
    git_dir = Path(repo_root) / ".git"
    if not git_dir.exists():
        return "skipped (not a git repo)"
    if git_dir.is_file():                      # worktree/submodule: .git is a file
        return "skipped (.git is a file — worktree/submodule)"
    src = _HERE / "scripts" / "post-commit"
    if not src.exists():
        return "skipped (post-commit script missing)"
    hooks = git_dir / "hooks"
    dst = hooks / "post-commit"
    marker = "claude-env"                       # our hooks carry this in a comment
    if dst.exists():
        try:
            existing = dst.read_text(errors="ignore")
        except Exception:
            existing = ""
        if marker not in existing:
            return "kept existing non-claude-env hook (install manually if wanted)"
        if existing == src.read_text():
            return "already installed"
    if not dry_run:
        hooks.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        dst.chmod(0o755)
    return "installed"


def _install_native_hooks(repo_root: str, dry_run: bool) -> str:
    """Install the policy + audit hooks into <repo>/.claude/settings.json so the
    native-tool governance is scoped to THIS onboarded repo (not machine-wide).
    Idempotent; merge-safe (preserves any existing settings). Also flags leftover
    machine-wide hooks so the operator can remove them."""
    installer = _HERE / "hooks" / "install_hooks.py"
    if not installer.exists():
        return "skipped (installer missing)"
    notice = ""
    gpath = Path.home() / ".claude" / "settings.json"
    if gpath.exists():
        try:
            g = json.loads(gpath.read_text())
            gtext = json.dumps(g.get("hooks", {}))
            if "policy_hook.py" in gtext or "audit_hook.py" in gtext:
                notice = ("  (note: machine-wide hooks still in ~/.claude/settings.json — "
                          "remove with `claude-env hooks --uninstall --global`)")
        except Exception:
            pass
    if dry_run:
        return f"would install into {repo_root}/.claude/settings.json{notice}"
    proc = subprocess.run(
        [sys.executable, str(installer), "--repo", str(repo_root)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        _log.error("native-hook install failed: %s", proc.stderr.strip())
        return f"FAILED ({proc.stderr.strip() or 'see logs'})"
    return f"installed into .claude/settings.json{notice}"


def _detect_test_command(repo_root: str) -> str | None:
    """Guess the repo's test command from its toolchain markers."""
    r = Path(repo_root)
    if (r / "go.mod").exists():
        return "go test ./..."
    if (r / "Cargo.toml").exists():
        return "cargo test"
    if (r / "package.json").exists():
        return "npm test"
    if any((r / f).exists() for f in ("pyproject.toml", "setup.py", "pytest.ini", "tox.ini")):
        return "pytest -q"
    if (r / "Makefile").exists():
        try:
            if any(ln.startswith("test:") for ln in (r / "Makefile").read_text().splitlines()):
                return "make test"
        except Exception:
            # unreadable Makefile — just don't infer a test command from it.
            _log.debug("could not read Makefile in %s for test-cmd inference", r,
                       exc_info=True)
    return None


def _install_commands(repo_root: str, dry_run: bool) -> str:
    """Write a <repo>/.claude/commands.json scaffold for the terminal MCP server.

    Always writes all three keys (run_tests / run_benchmarks / run_audit). The
    test command is auto-filled when a single toolchain is detected; anything not
    detected is left as "" — which the terminal server treats as 'not configured'
    (it reports NOT CONFIGURED and runs nothing) for the user to fill in. Never
    clobbers an existing file."""
    dst = Path(repo_root) / ".claude" / "commands.json"
    if dst.exists():
        return "kept existing commands.json"
    detected = _detect_test_command(repo_root)
    scaffold = {
        "_comment": ("Commands for terminal.run_tests / run_benchmarks / run_audit. "
                     "Each runs as a plain argv list from the repo root — no cd, &&, "
                     "pipes, or shell vars. Examples: 'go test ./...', 'npm test', "
                     "'pytest -q', 'cargo test', 'make -C <subdir> test'. Leave a value "
                     "empty to keep it unconfigured (the tool then runs nothing)."),
        "run_tests": detected or "",
        "run_benchmarks": "",
        "run_audit": "",
    }
    if not dry_run:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(json.dumps(scaffold, indent=2) + "\n")
    return (f"scaffold written (run_tests={detected})" if detected
            else "scaffold written (fill in run_tests/benchmarks/audit)")


def _maybe_index(repo_root: str, slug: str, branch: str, interactive: bool,
                 dry_run: bool) -> None:
    """Offer to build the initial RAG index now (opt-in; it needs the embedding
    model and can be slow on large repos)."""
    if dry_run or not interactive:
        if not interactive:
            print(f"\nNext: build the RAG index when ready -> "
                  f"{GREEN}claude-env index {repo_root}{RESET}")
        return
    ans = _prompt("Build the RAG index for this repo now? (y/N)", "N")
    if ans.lower() not in ("y", "yes"):
        print(f"  skipped — run {GREEN}claude-env index {repo_root}{RESET} later")
        return
    script = _HERE / "rag" / "bootstrap_rag.py"
    env = {**os.environ,
           "CLAUDE_ENV_REPO_NAME": slug, "CLAUDE_ENV_BRANCH": branch}
    print(f"  indexing {repo_root} …")
    rc = subprocess.run([sys.executable, str(script), repo_root], env=env).returncode
    print(f"  {'indexed' if rc == 0 else 'indexing reported errors (see above)'}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Onboard a repo to claude-env: provision its isolated RAG/memory "
                    "namespace + policy, patch MCP env, and install the CLAUDE.md "
                    "governance template + engineering skills.")
    parser.add_argument("repo_root", help="Absolute path to the repository root")
    parser.add_argument("--repo-name", default=None,
                        help="Short repo slug (RAG/memory namespace); defaults to dir name")
    parser.add_argument("--tier", default=None, choices=["0", "1", "2", "3"],
                        help="Privacy tier (0 public..3 restricted); prompted if omitted")
    parser.add_argument("--description", default=None,
                        help="Short human-readable purpose of the repo")
    parser.add_argument("--branch", default=None,
                        help="Default branch for lancedb-rag (auto-detected if omitted)")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Non-interactive: accept detected defaults, no prompts")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would change without writing anything")
    parser.add_argument("--no-template", action="store_true",
                        help="Skip installing CLAUDE.md + .claude/ skills & agents")
    parser.add_argument("--force-template", action="store_true",
                        help="Overwrite existing .claude/ skill & agent files")
    parser.add_argument("--force-policy", action="store_true",
                        help="Overwrite an existing .claude/repo-policy.yaml")
    parser.add_argument("--no-post-commit", action="store_true",
                        help="Skip installing the git post-commit RAG re-index hook")
    args = parser.parse_args()

    repo_root = str(Path(args.repo_root).resolve())
    if not Path(repo_root).is_dir():
        print(f"{RED}ERROR{RESET} repo path does not exist: {repo_root}")
        return 1

    interactive = _interactive(args.yes)

    # --- gather details (detected defaults, refined interactively) -----------
    slug    = _slugify(args.repo_name or Path(repo_root).name)
    branch  = args.branch or _detect_branch(repo_root)
    tier    = args.tier or _detect_tier(repo_root)   # existing policy wins as default
    desc    = args.description or ""

    print(f"\n{GREEN}== claude-env onboarding =={RESET}  {repo_root}")
    if interactive:
        print("Answer a few questions (Enter accepts the default):\n")
        slug   = _slugify(_prompt("Repo slug (used for RAG + memory namespaces)", slug))
        tier   = _prompt_tier(tier)
        branch = _prompt("Default branch", branch)
        desc   = _prompt("Short description (optional)", desc)
        print()

    tier_i     = int(tier)
    table      = _provision_storage(slug, branch, args.dry_run)
    memory_ns  = f"proj-{slug}"
    isolated   = tier_i >= 2
    tag        = f"{YELLOW}DRY RUN{RESET} " if args.dry_run else ""

    # 1. isolation boundary: the repo policy (creates .claude/repo-policy.yaml)
    pol = _write_repo_policy(repo_root, slug, tier, desc, args.force_policy, args.dry_run)
    print(f"{tag}repo-policy.yaml (tier {tier}): {pol}")

    # 2. MCP env + namespaces in ~/.claude.json
    blocks = _build_server_blocks(repo_root, slug, branch, tier_i)
    _patch_claude_json(repo_root, blocks, args.dry_run)

    # 3. governance template with CONCRETE namespace values (no placeholders left)
    if not args.no_template:
        print()
        subs = {
            "REPO_NAME": slug, "TIER": tier, "BRANCH": branch,
            "RAG_TABLE": table, "MEMORY_NS": memory_ns,
            "MEMORY_ISOLATED": "disabled (isolated)" if isolated else "allowed",
        }
        _install_template(repo_root, subs, args.force_template, args.dry_run)

    # 4. git post-commit hook -> incremental RAG re-index on every commit
    tag = f"{YELLOW}DRY RUN{RESET} " if args.dry_run else ""
    if not args.no_post_commit:
        print(f"\n{tag}post-commit hook: {_install_post_commit(repo_root, args.dry_run)}")

    # 4b. terminal test command (so terminal.run_tests fits the repo's toolchain)
    print(f"{tag}commands.json: {_install_commands(repo_root, args.dry_run)}")

    # 4c. native-tool governance hooks -> repo-local .claude/settings.json.
    # Part of the in-repo .claude/ deliverable, so it follows --no-template.
    if not args.no_template:
        print(f"{tag}native-tool hooks: {_install_native_hooks(repo_root, args.dry_run)}")

    # 5. summary of the isolated space
    print(f"\n{GREEN}Isolated workspace provisioned:{RESET}")
    print(f"  slug        : {slug}")
    print(f"  tier        : {tier}")
    print(f"  branch      : {branch}")
    print(f"  RAG table   : {table}")
    print(f"  memory ns   : {memory_ns}  (cross-project reads: "
          f"{'disabled' if isolated else 'allowed'})")

    # 6. optional first index
    _maybe_index(repo_root, slug, branch, interactive, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
