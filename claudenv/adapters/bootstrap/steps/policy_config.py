"""
claude-env :: Adapters - Bootstrap Steps - Policy Config
"""
from __future__ import annotations

from pathlib import Path

from claudenv.ports.bootstrap import BootstrapContext, BootstrapResult


class PolicyConfigStep:
    """Step 7: Deploy policy configs and mirror platform code."""

    @property
    def name(self) -> str:
        return "policy-config"

    @property
    def description(self) -> str:
        return "Deploy policy configs and mirror platform code"

    def _deploy_config(self, src: Path, dst: Path, label: str,
                       needed_by: str | None = None, force: bool = False) -> list[str]:
        warnings = []
        if not src.exists():
            suffix = f" ({needed_by} will fail until present)" if needed_by else ""
            warnings.append(f"{src.name} missing in repo{suffix}")
            return warnings
        if dst.exists() and not force:
            warnings.append(f"{label} -> {dst} (already present, left untouched; use --force-config to reset)")
            return warnings
        import shutil
        shutil.copy2(src, dst)
        return []

    def execute(self, context: BootstrapContext) -> BootstrapResult:
        warnings = []
        repo_dir = Path(context.repo_dir)
        home = Path(context.home)

        # Deploy global policy
        warnings.extend(self._deploy_config(
            repo_dir / "config" / "global-policy.yaml",
            home / "config" / "global-policy.yaml",
            "global policy",
            force=context.force_config,
        ))

        # Deploy repo-policy template
        tmpl = repo_dir / "config" / "repo-policy.template.yaml"
        dst_tmpl = home / "config" / "repo-policy.template.yaml"
        if tmpl.exists():
            import shutil
            shutil.copy2(tmpl, dst_tmpl)
            # ok

        # Deploy RAG model config
        warnings.extend(self._deploy_config(
            repo_dir / "config" / "rag.yaml",
            home / "config" / "rag.yaml",
            "rag model config",
            force=context.force_config,
        ))

        # Deploy configs needed by deployed scripts
        for name, needed_by in (("mcp-servers.json", "repo onboarding"),
                                 ("budgets.yaml", "claude-env budget")):
            warnings.extend(self._deploy_config(
                repo_dir / "config" / name,
                home / "config" / name,
                name,
                needed_by=needed_by,
                force=context.force_config,
            ))

        # Mirror platform code
        for sub in ("claudenv", "sql", "templates"):
            s = repo_dir / sub
            d = home / sub
            if s.exists():
                import shutil
                if d.exists():
                    shutil.rmtree(d)
                shutil.copytree(s, d, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"))

        # Clean stale scratch dirs
        removed = self._reap_stale_scratch(home)
        if removed:
            pass  # OK

        # Install CLI
        src_cli = repo_dir / "claudenv" / "bin" / "claude-env"
        if src_cli.exists():
            dst_cli = home / "bin" / "claude-env"
            import shutil
            shutil.copy2(src_cli, dst_cli)
            dst_cli.chmod(0o755)

        return BootstrapResult.ok(f"platform code mirrored under {home}", warnings)

    def _reap_stale_scratch(self, home: Path, ttl_hours: float = 24) -> list[str]:
        import time
        scratch_root = home / "scratch"
        if not scratch_root.is_dir():
            return []
        cutoff = time.time() - ttl_hours * 3600
        removed = []
        for child in scratch_root.iterdir():
            if child.is_dir() and child.stat().st_mtime < cutoff:
                import shutil
                shutil.rmtree(child, ignore_errors=True)
                removed.append(child.name)
        return removed

    def can_skip(self, context: BootstrapContext) -> bool:
        return False  # Idempotent with force flag
