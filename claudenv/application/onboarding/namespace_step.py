"""
claude-env :: Application - Onboarding Service - NamespaceStep
"""
from __future__ import annotations

from pathlib import Path

from claudenv.domain.value_objects import Tier

from claudenv.application.onboarding.context import OnboardingContext
from claudenv.application.onboarding.step import OnboardingStep


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
