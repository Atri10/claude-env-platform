"""claude-env :: CLI - onboard command."""
from __future__ import annotations

import logging
import sys

import click

from claudenv.adapters.config import get_config
from claudenv.application.onboarding import OnboardingService
from claudenv.domain.value_objects import Tier

logger = logging.getLogger(__name__)


def _prompt_onboarding_inputs(
    repo_root, repo_name, tier, description, branch, non_interactive, service,
):
    from pathlib import Path as P

    if not repo_root:
        repo_root = click.prompt(
            "Repository path",
            type=click.Path(exists=True, file_okay=False, resolve_path=True),
        )
    repo_path = P(repo_root).resolve()

    detected_branch = branch or service._detect_branch(repo_path)
    detected_tier = tier
    if detected_tier is None:
        detected_tier_obj = service._detect_tier(repo_path)
        detected_tier = str(int(detected_tier_obj))
    detected_slug = repo_name or repo_path.name

    if non_interactive:
        return repo_root, repo_name, tier, description, branch

    click.echo("\n=== claude-env Interactive Onboarding ===")
    click.echo(f"Repository: {repo_path}")
    click.echo()

    if not repo_name:
        repo_name = click.prompt(
            "Repo slug (namespace-safe identifier)", default=detected_slug, show_default=True,
        )
    if tier is None:
        tier_labels = {
            "0": "public (open source, no secrets)",
            "1": "internal (company-internal, no customer data)",
            "2": "sensitive (PII, secrets, credentials)",
            "3": "restricted (regulated, classified)",
        }
        click.echo("Privacy tier:")
        for k, v in tier_labels.items():
            default_marker = " (default)" if k == detected_tier else ""
            click.echo(f"  {k} - {v}{default_marker}")
        tier = click.prompt(
            "Select tier [0-3]", default=detected_tier, show_default=True,
            type=click.Choice(["0", "1", "2", "3"]),
        )
    if not description:
        description = click.prompt("Short description (optional)", default=description, show_default=False)
    if not branch:
        branch = click.prompt("Default branch", default=detected_branch, show_default=True)

    click.echo()
    click.echo(f"  Repo:     {repo_path}")
    click.echo(f"  Slug:     {repo_name}")
    click.echo(f"  Tier:     {tier} ({Tier(int(tier)).label})")
    click.echo(f"  Branch:   {branch}")
    if description:
        click.echo(f"  Desc:     {description}")
    click.echo()
    if not click.confirm("Proceed with onboarding?", default=True):
        click.echo("Aborted.")
        ctx = click.get_current_context()
        ctx.exit(0)
    return repo_root, repo_name, tier, description, branch


@click.command("onboard")
@click.argument("repo_root", type=click.Path(exists=True, file_okay=False, resolve_path=True), required=False)
@click.option("--repo-name", help="Repo slug for namespaces")
@click.option("--tier", type=click.Choice(["0", "1", "2", "3"]), help="Privacy tier")
@click.option("--description", default="", help="Short description")
@click.option("--branch", help="Default branch")
@click.option("--interactive", "-i", is_flag=True, help="Force interactive mode")
@click.option("--yes", "-y", is_flag=True, help="Non-interactive (accept defaults)")
@click.option("--dry-run", is_flag=True, help="Print what would change")
@click.option("--no-template", is_flag=True, help="Skip CLAUDE.md and skills")
@click.option("--force-template", is_flag=True, help="Overwrite existing skills/agents")
@click.option("--force-policy", is_flag=True, help="Regenerate repo-policy.yaml")
@click.option("--no-post-commit", is_flag=True, help="Skip git hooks")
@click.pass_context
def onboard(ctx, repo_root, repo_name, tier, description, branch, interactive, yes, dry_run,
            no_template, force_template, force_policy, no_post_commit):
    """Onboard a repository to claude-env."""
    config = get_config()
    service = OnboardingService(config)

    is_tty = sys.stdin.isatty()
    use_interactive = interactive or (is_tty and not yes)
    logger.debug("[flow] onboard: start repo_root=%s use_interactive=%s dry_run=%s", repo_root, use_interactive, dry_run)

    if use_interactive:
        repo_root, repo_name, tier, description, branch = _prompt_onboarding_inputs(
            repo_root, repo_name, tier, description, branch, yes, service,
        )

    if not repo_root:
        click.echo("ERROR: Repository path is required", err=True)
        ctx.exit(1)

    result = service.onboard(
        repo_root=repo_root, slug=repo_name, tier=int(tier) if tier else None,
        branch=branch, description=description, dry_run=dry_run,
        force_policy=force_policy, force_template=force_template,
        no_template=no_template, no_post_commit=no_post_commit,
    )

    if dry_run:
        click.echo("DRY RUN - nothing written")
    else:
        click.echo(f"Onboarded: {result.slug} (tier {result.tier})")
        click.echo(f"  RAG table: {result.rag_table}")
        click.echo(f"  Memory ns: {result.memory_namespace} (isolated={result.memory_isolated})")
    logger.debug("[flow] onboard: complete slug=%s tier=%s dry_run=%s", result.slug, result.tier, dry_run)
