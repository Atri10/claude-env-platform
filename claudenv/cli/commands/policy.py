"""claude-env :: CLI - validate, policy-sim commands."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import click
import yaml

from claudenv.adapters.config import get_config

logger = logging.getLogger(__name__)


@click.group("validate")
def validate():
    """Run validation checks."""
    logger.debug("[flow] group: validate")


@validate.command("installation")
@click.pass_context
def validate_installation(ctx):
    """Validate full installation."""
    config = get_config()
    logger.debug("[flow] validate installation: start")
    home = Path(config.get_claude_env_home())
    checks = [
        ("venv exists", (home / "venv" / "bin" / "python").exists()),
        ("config dir", (home / "config").exists()),
        ("global policy", (home / "config" / "global-policy.yaml").exists()),
        ("rag.yaml", (home / "config" / "rag.yaml").exists()),
        ("mcp-servers.json", (home / "config" / "mcp-servers.json").exists()),
        ("knowledge dir", (home / "knowledge" / "lancedb").exists()),
    ]
    all_pass = True
    for name, ok in checks:
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_pass = False
        click.echo(f"  [{status}] {name}")
    if all_pass:
        click.echo("\nAll checks passed!")
    else:
        click.echo("\nSome checks failed!", err=True)
        sys.exit(1)


@click.group("policy-sim")
def policy_sim():
    """Dry-run a candidate policy before it goes live."""
    logger.debug("[flow] group: policy_sim")


@policy_sim.command("simulate")
@click.argument("repo_root", type=click.Path(exists=True, file_okay=False, resolve_path=True))
@click.option("--candidate", required=True,
              type=click.Path(exists=True, dir_okay=False, resolve_path=True),
              help="Candidate repo-policy YAML to evaluate (dry-run).")
@click.pass_context
def policy_sim_simulate(ctx, repo_root, candidate):
    """Show what a candidate repo policy would change (dry-run, nothing written)."""
    logger.debug("[flow] policy sim simulate: start repo_root=%s candidate=%s", repo_root, candidate)

    from claudenv.application.policy import PolicyService
    candidate_data = yaml.safe_load(Path(candidate).read_text()) or {}
    service = PolicyService(get_config())
    result = service.simulate(repo_root, candidate_data)

    click.echo(f"# policy simulation — {result['repo']}")
    click.echo(
        f"tier: {result['tier_current']} -> {result['tier_candidate']}   "
        f"files: {result['files']}   "
        f"blocked: {result['blocked_current']} -> {result['blocked_candidate']}"
    )
    if result["newly_blocked"]:
        click.echo(f"\nNEWLY BLOCKED: {len(result['newly_blocked'])}")
        for f, _reason, rule in result["newly_blocked"][:20]:
            click.echo(f"  {f}  (deny: {rule})")
    if result["newly_allowed"]:
        click.echo(f"\nNEWLY ALLOWED: {len(result['newly_allowed'])}")
        for f, _reason, rule in result["newly_allowed"][:20]:
            click.echo(f"  {f}  (allow: {rule})")
    if result["changed_rule"]:
        click.echo(f"\nBLOCKED BY DIFFERENT RULE: {len(result['changed_rule'])}")
        for f, cur, can in result["changed_rule"][:20]:
            click.echo(f"  {f}  ({cur} -> {can})")
