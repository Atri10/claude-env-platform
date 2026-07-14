"""
claude-env :: Application - Onboarding Service - MCPEnvStep
"""
from __future__ import annotations

import json
from pathlib import Path

from claudenv.application.onboarding.context import OnboardingContext
from claudenv.application.onboarding.step import OnboardingStep


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
