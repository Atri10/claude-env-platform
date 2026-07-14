"""
claude-env :: Application - Onboarding Service - concrete steps

All nine concrete OnboardingStep implementations, executed in order by
OnboardingService: PolicyStep, NamespaceStep, MCPEnvStep, TemplateStep,
GitHooksStep, CommandsStep, NativeHooksStep, AgentsStep, IndexStep.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from claudenv.domain.value_objects import Tier
from claudenv._data import config_dir, templates_dir

from claudenv.application.onboarding.base import OnboardingContext, OnboardingStep


def _resolve_deployed_or_packaged(home_rel: Path, packaged: Path) -> Path:
    """Return the deployed copy under $CLAUDE_ENV_HOME if present, else packaged.

    ``home_rel`` is an already-joined path under $CLAUDE_ENV_HOME; ``packaged``
    is the fallback shipped in the wheel.
    """
    return home_rel if home_rel.exists() else packaged


class PolicyStep(OnboardingStep):
    """Create repo policy from template."""

    def execute(self, ctx: OnboardingContext) -> bool:
        if ctx.no_template:
            ctx.add_step("policy", skipped=True)
            return False

        dst = Path(ctx.repo_root) / ".claude" / "repo-policy.yaml"
        if dst.exists() and not ctx.force_policy:
            ctx.add_step("policy", skipped=True)
            return False

        tmpl = _resolve_deployed_or_packaged(
            Path(self.config.get_claude_env_home()) / "config" / "repo-policy.template.yaml",
            config_dir() / "repo-policy.template.yaml",
        )
        if not tmpl.exists():
            ctx.add_step("policy", skipped=True)
            return False

        text = tmpl.read_text()
        text = text.replace("EXAMPLE-REPO-SLUG", str(ctx.slug))
        text = re.sub(r"(?m)^tier:\s*\d+", f"tier: {int(ctx.tier)}", text, count=1)
        if ctx.description:
            safe = ctx.description.replace('"', "'")
            text = re.sub(r'(?m)^description:\s*".*"', f'description: "{safe}"', text, count=1)
        if ctx.tier in (Tier.SENSITIVE, Tier.RESTRICTED):
            text = re.sub(r"(?m)^(\s*isolated:\s*)false", r"\g<1>true", text, count=1)

        if not ctx.dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(text)

        ctx.add_step("policy")
        return True


class NamespaceStep(OnboardingStep):
    """Provision RAG table and memory namespace."""

    def execute(self, ctx: OnboardingContext) -> bool:
        # RAG table name
        safe = lambda s: s.replace("/", "-").replace(" ", "_")
        ctx.rag_table = f"{safe(str(ctx.slug))}__{safe(str(ctx.branch))}"

        # Memory namespace
        ctx.memory_ns = f"proj-{ctx.slug}"
        ctx.memory_isolated = ctx.tier >= Tier.SENSITIVE

        # Provision LanceDB directory
        lancedb_dir = Path(self.config.get_lancedb_path())
        if not ctx.dry_run:
            lancedb_dir.mkdir(parents=True, exist_ok=True)

        ctx.add_step("namespaces")
        return True


class MCPEnvStep(OnboardingStep):
    """Patch ~/.claude.json with MCP server env vars."""

    def execute(self, ctx: OnboardingContext) -> bool:
        claude_json = Path.home() / ".claude.json"
        if not claude_json.exists():
            ctx.add_step("mcp_env", skipped=True)
            return False

        # Read MCP config
        mcp_config = Path(self.config.get_claude_env_home()) / "config" / "mcp-servers.json"
        if not mcp_config.exists():
            ctx.add_step("mcp_env", skipped=True)
            return False

        cfg = json.loads(mcp_config.read_text())
        defaults_env = cfg.get("defaults", {}).get("env", {})

        subs = {
            "HOME": str(Path.home()),
            "CLAUDE_ENV_HOME": self.config.get_claude_env_home(),
            "workspaceFolder": ctx.repo_root,
            "CLAUDE_ENV_REPO_ROOT": ctx.repo_root,
            "CLAUDE_ENV_REPO_NAME": str(ctx.slug),
            "CLAUDE_ENV_BRANCH": str(ctx.branch),
        }

        def resolve_env(env: dict) -> dict:
            return {k: self._resolve(v, subs) for k, v in env.items()}

        def _resolve(v: str, subs: dict) -> str:
            for k, val in subs.items():
                v = v.replace(f"${{{k}}}", val)
            return v

        data = json.loads(claude_json.read_text())
        projects = data.setdefault("projects", {})
        project = projects.setdefault(ctx.repo_root, {})
        existing = project.setdefault("mcpServers", {})

        updated = []
        for name, srv in cfg.get("mcpServers", {}).items():
            if name == "jetbrains" or srv.get("optional"):
                continue

            merged_env = {**defaults_env, **srv.get("env", {})}
            resolved_env = resolve_env(merged_env)

            if name == "lancedb-rag":
                resolved_env.setdefault("CLAUDE_ENV_REPO_NAME", str(ctx.slug))
                resolved_env.setdefault("CLAUDE_ENV_BRANCH", str(ctx.branch))
            if name == "memory-graph":
                resolved_env["CLAUDE_ENV_MEMORY_NS"] = f"proj-{ctx.slug}"
                resolved_env["CLAUDE_ENV_MEMORY_ISOLATED"] = "true" if ctx.memory_isolated else "false"
            if name == "documentation":
                resolved_env.setdefault("CLAUDE_ENV_DOCS_DIR",
                                        str(Path(self.config.get_claude_env_home()) / "knowledge" / "docs"))

            if existing.get(name, {}).get("env") != resolved_env:
                updated.append(name)
            existing[name] = {
                "type": "stdio",
                "command": self._resolve(srv["command"], subs),
                "args": [self._resolve(a, subs) for a in srv.get("args", [])],
                "env": resolved_env,
            }

        if not ctx.dry_run and updated:
            claude_json.write_text(json.dumps(data, indent=2) + "\n")
            print(f"  Updated env vars for: {', '.join(updated)}")
            print(f"  ACTION REQUIRED: Restart Claude Code")

        ctx.add_step("mcp_env")
        return True


class TemplateStep(OnboardingStep):
    """Install CLAUDE.md and .claude/ skills/agents."""

    def execute(self, ctx: OnboardingContext) -> bool:
        if ctx.no_template:
            ctx.add_step("template", skipped=True)
            return False

        template_dir = _resolve_deployed_or_packaged(
            Path(self.config.get_claude_env_home()) / "templates" / "repo-onboarding",
            templates_dir() / "repo-onboarding",
        )
        if not template_dir.exists():
            ctx.add_step("template", skipped=True)
            return False

        subs = {
            "REPO_NAME": str(ctx.slug),
            "TIER": str(int(ctx.tier)),
            "BRANCH": str(ctx.branch),
            "RAG_TABLE": ctx.rag_table,
            "MEMORY_NS": ctx.memory_ns,
            "MEMORY_ISOLATED": "disabled (isolated)" if ctx.memory_isolated else "allowed",
        }

        # CLAUDE.md
        src = template_dir / "CLAUDE.md"
        dst = Path(ctx.repo_root) / "CLAUDE.md"
        if src.exists():
            managed = self._fill_template(src.read_text(), subs)
            if dst.exists():
                current = dst.read_text()
                if "<!-- CLAUDE-ENV:BEGIN (managed)" in current and "<!-- CLAUDE-ENV:END -->" in current:
                    # Replace managed block
                    import re
                    pattern = re.compile(
                        re.escape("<!-- CLAUDE-ENV:BEGIN (managed)") + r".*?" +
                        re.escape("<!-- CLAUDE-ENV:END -->"), re.DOTALL
                    )
                    new_block = managed[managed.index("<!-- CLAUDE-ENV:BEGIN (managed)"):
                                        managed.index("<!-- CLAUDE-ENV:END -->") + len("<!-- CLAUDE-ENV:END -->")]
                    merged = pattern.sub(new_block, current)
                else:
                    merged = managed.rstrip() + "\n\n" + current.lstrip()
            else:
                merged = managed

            if not ctx.dry_run:
                dst.write_text(merged)

        # .claude/skills and .claude/agents
        src_root = template_dir / ".claude"
        if src_root.exists():
            for src in src_root.rglob("*"):
                if not src.is_file():
                    continue
                if src.name == ".DS_Store" or "__pycache__" in src.parts or src.suffix == ".pyc":
                    continue
                rel = src.relative_to(template_dir)
                dst = Path(ctx.repo_root) / rel
                if dst.exists() and not ctx.force_template:
                    continue
                if not ctx.dry_run:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)

        ctx.add_step("template")
        return True

    def _fill_template(self, text: str, subs: dict) -> str:
        for k, v in subs.items():
            text = text.replace(f"{{{{{k}}}}}", v)
        return text


class GitHooksStep(OnboardingStep):
    """Install git hooks for auto-reindex."""

    def execute(self, ctx: OnboardingContext) -> bool:
        if ctx.no_post_commit:
            ctx.add_step("git_hooks", skipped=True)
            return False

        git_dir = Path(ctx.repo_root) / ".git"
        if not git_dir.exists() or git_dir.is_file():
            ctx.add_step("git_hooks", skipped=True)
            return False

        hook_names = ("post-commit", "post-merge", "post-checkout")
        hook_src_dir = Path(self.config.get_claude_env_home()) / "scripts"
        installed = []

        for name in hook_names:
            src = hook_src_dir / name
            if not src.exists():
                continue
            dst = git_dir / "hooks" / name
            if dst.exists():
                existing = dst.read_text(errors="ignore")
                if "claude-env" not in existing:
                    installed.append(f"{name} (kept existing)")
                    continue
            if not ctx.dry_run:
                shutil.copy2(src, dst)
                dst.chmod(0o755)
            installed.append(name)

        if installed:
            print(f"  Installed hooks: {', '.join(installed)}")
        else:
            print("  All hooks already installed")

        ctx.add_step("git_hooks")
        return True


class CommandsStep(OnboardingStep):
    """Create .claude/commands.json for terminal MCP server."""

    def execute(self, ctx: OnboardingContext) -> bool:
        dst = Path(ctx.repo_root) / ".claude" / "commands.json"
        if dst.exists():
            ctx.add_step("commands", skipped=True)
            return False

        # Detect test command
        r = Path(ctx.repo_root)
        test_cmd = None
        if (r / "go.mod").exists():
            test_cmd = "go test ./..."
        elif (r / "Cargo.toml").exists():
            test_cmd = "cargo test"
        elif (r / "package.json").exists():
            test_cmd = "npm test"
        elif any((r / f).exists() for f in ("pyproject.toml", "setup.py", "pytest.ini", "tox.ini")):
            test_cmd = "pytest -q"

        scaffold = {
            "_comment": "Commands for terminal.run_tests / run_benchmarks / run_audit",
            "run_tests": test_cmd or "",
            "run_benchmarks": "",
            "run_audit": "",
        }

        if not ctx.dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(json.dumps(scaffold, indent=2) + "\n")

        ctx.add_step("commands")
        return True


class NativeHooksStep(OnboardingStep):
    """Install native tool governance hooks."""

    def execute(self, ctx: OnboardingContext) -> bool:
        if ctx.no_template:
            ctx.add_step("native_hooks", skipped=True)
            return False

        installer = Path(self.config.get_claude_env_home()) / "hooks" / "install_hooks.py"
        if not installer.exists():
            ctx.add_step("native_hooks", skipped=True)
            return False

        if not ctx.dry_run:
            result = subprocess.run(
                [sys.executable, str(installer), "--repo", ctx.repo_root],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                print(f"  WARNING: hook install failed: {result.stderr.strip()}")

        ctx.add_step("native_hooks")
        return True


class AgentsStep(OnboardingStep):
    """Install specialist agents."""

    def execute(self, ctx: OnboardingContext) -> bool:
        if ctx.no_template:
            ctx.add_step("agents", skipped=True)
            return False

        src_dir = _resolve_deployed_or_packaged(
            Path(self.config.get_claude_env_home()) / "templates" / "repo-onboarding" / ".claude" / "agents",
            templates_dir() / "repo-onboarding" / ".claude" / "agents",
        )
        dst_dir = Path(ctx.repo_root) / ".claude" / "agents"
        if not src_dir.exists():
            ctx.add_step("agents", skipped=True)
            return False

        agent_files = list(src_dir.glob("*.md"))
        if not agent_files:
            ctx.add_step("agents", skipped=True)
            return False

        installed = 0
        for src in sorted(agent_files):
            dst = dst_dir / src.name
            if dst.exists():
                continue
            if not ctx.dry_run:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            installed += 1

        if installed:
            print(f"  Installed {installed} specialist agents")
        ctx.add_step("agents")
        return True


class IndexStep(OnboardingStep):
    """Offer to build initial RAG index."""

    def execute(self, ctx: OnboardingContext) -> bool:
        if ctx.dry_run or not sys.stdin.isatty() or not sys.stdout.isatty():
            print(f"  Next: build the RAG index when ready -> claude-env index {ctx.repo_root}")
            ctx.add_step("index", skipped=True)
            return False

        try:
            ans = input("  Build the RAG index for this repo now? (y/N): ").strip().lower()
        except EOFError:
            ans = "n"

        if ans not in ("y", "yes"):
            print(f"  skipped — run {ctx.repo_root} later")
            ctx.add_step("index", skipped=True)
            return False

        script = Path(self.config.get_claude_env_home()) / "rag" / "bootstrap_rag.py"
        if not script.exists():
            print("  index script not found")
            ctx.add_step("index", skipped=True)
            return False

        env = {**os.environ, "CLAUDE_ENV_REPO_NAME": str(ctx.slug), "CLAUDE_ENV_BRANCH": str(ctx.branch)}
        print(f"  indexing {ctx.repo_root} …")
        rc = subprocess.run([sys.executable, str(script), ctx.repo_root], env=env).returncode
        print(f"  {'indexed' if rc == 0 else 'indexing reported errors (see above)'}")
        ctx.add_step("index")
        return True
