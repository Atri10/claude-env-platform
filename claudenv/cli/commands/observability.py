"""claude-env :: CLI - budget, dashboard, feedback commands."""
from __future__ import annotations

import json
import logging
import sys

import click

from claudenv.application.observability import BudgetService, DashboardService, FeedbackService
from claudenv.di import get_container

logger = logging.getLogger(__name__)


@click.command("budget")
@click.option("--repo", help="Filter to one repo")
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text")
@click.pass_context
def budget(ctx, repo, fmt):
    """Check month-to-date spend against configured budgets (advisory)."""
    logger.debug("[flow] budget: start repo=%s format=%s", repo, fmt)
    svc = get_container().get(BudgetService)
    result = svc.evaluate(repo=repo)

    if fmt == "json":
        click.echo(json.dumps(result.to_dict(), indent=2))
    else:
        if not result.repos:
            click.echo("No session cost data yet.")
        else:
            click.echo(f"{'repo':<24} {'sessions':>8} {'spent':>10} {'budget':>10} {'pct':>6}  status")
            for r in result.repos:
                budget_s = f"${r.budget_usd:.2f}" if r.budget_usd else "-"
                pct_s = f"{r.pct * 100:.0f}%" if r.pct is not None else "-"
                click.echo(
                    f"{r.repo:<24} {r.sessions:>8} ${r.spent_usd:>8.2f} {budget_s:>10} "
                    f"{pct_s:>6}  {r.status.value}"
                )
        click.echo(f"\noverall: {result.overall.value}")
    if result.overall.value == "EXCEEDED":
        sys.exit(1)


@click.command("dashboard")
@click.option("--window", default="30d", help="Time window (e.g. 24h, 7d, 30d)")
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text")
@click.pass_context
def dashboard(ctx, window, fmt):
    """Read-only operational summary (costs, latency, retrieval quality, violations)."""
    logger.debug("[flow] dashboard: start window=%s format=%s", window, fmt)
    svc = get_container().get(DashboardService)
    summary = svc.summary(window=window)

    if fmt == "json":
        click.echo(json.dumps(summary, indent=2, default=str))
        return

    click.echo(f"== claude-env dashboard (window={window}) ==\n")
    click.echo("Top session costs:")
    for row in summary["top_session_costs"]:
        click.echo(f"  {row['repo']:<20} ${row['spent_usd']:>8}  {row['session_id']}")
    click.echo("\nCost by repo (total):")
    for row in summary.get("cost_by_repo", []):
        click.echo(f"  {row.repo:<20} ${row.spent_usd:>8}  ({row.sessions} sessions)")
    click.echo("\nLatency by component (ms):")
    for component, p in summary["latency"].items():
        click.echo(f"  {component:<20} p50={p['p50']:.0f} p95={p['p95']:.0f} max={p['max']:.0f} (n={p['count']})")
    click.echo("\nRetrieval quality (mean top-1 by repo):")
    for row in summary["retrieval_quality"]:
        click.echo(f"  {row['repo']:<20} {row['mean_top1']}  (n={row['queries']})")
    click.echo("\nRecent policy violations:")
    for row in summary["policy_violations"]:
        click.echo(f"  [{row['ts']}] {row['decision']} {row['path']} ({row['rule']})")
    click.echo("\nSecurity events:")
    for row in summary["security_events"]:
        click.echo(f"  {row['severity']:<8} {row['category']:<18} {row['n']}")
    click.echo("\nOpen approvals:")
    for row in summary["open_approvals"]:
        click.echo(f"  {row['request_id']}  {row['agent']}  {row['action']}")


@click.command("feedback")
@click.option("--repo", help="Filter to one repo")
@click.pass_context
def feedback(ctx, repo):
    """Show RAG retrieval feedback stats (retrieved/used counts, top files)."""
    logger.debug("[flow] feedback: start repo=%s", repo)
    svc = get_container().get(FeedbackService)
    stats = svc.stats(repo=repo)
    click.echo(f"retrieved: {stats.get('retrieved', 0)}")
    click.echo(f"used:      {stats.get('used', 0)}")
    top = stats.get("top_used_files", [])
    if top:
        click.echo("top used files:")
        for path, n in top:
            click.echo(f"  {n:>4}  {path}")
