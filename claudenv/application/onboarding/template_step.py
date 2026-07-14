"""
claude-env :: Application - Onboarding Service - TemplateStep
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from claudenv.application.onboarding.context import OnboardingContext
from claudenv.application.onboarding.step import OnboardingStep


class TemplateStep(OnboardingStep):
    """Install CLAUDE.md and .claude/ skills/agents."""

    def execute(self, ctx: OnboardingContext) -> bool:
        if ctx.no_template:
            ctx.add_step("template", skipped=True)
            return False

        template_dir = Path(self.config.get_claude_env_home()) / "templates" / "repo-onboarding"
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
