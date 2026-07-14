"""
claude-env :: Application - Onboarding Service - PolicyStep
"""
from __future__ import annotations

import re
from pathlib import Path

from claudenv.domain.value_objects import Tier

from claudenv.application.onboarding.context import OnboardingContext
from claudenv.application.onboarding.step import OnboardingStep


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

        tmpl = Path(self.config.get_claude_env_home()) / "config" / "repo-policy.template.yaml"
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
