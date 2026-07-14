"""
claude-env :: CLI Entry Point
"""
from __future__ import annotations

import click
import os
import sys
from pathlib import Path

from claudenv.adapters.config import get_config
from claudenv.application.audit import ComplianceReportGenerator, SessionReplay
from claudenv.application.observability import BudgetService, DashboardService, FeedbackService
from claudenv.application.onboarding import OnboardingService
from claudenv.application.rag import RagService
from claudenv.di import get_container
from claudenv.ports.audit import IAuditRepository
from claudenv.ports.database import IDatabase


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")
@click.pass_context
def cli(ctx, verbose):
    """claude-env - Local-first AI governance platform."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose


@cli.command()
@click.option("--with-brew", is_flag=True, help="Install system dependencies via Homebrew")
@click.option("--no-deps", is_flag=True, help="Skip pip install (venv must exist)")
@click.option("--no-venv-create", is_flag=True, help="Reuse existing venv, just update deps")
@click.option("--recreate-venv", is_flag=True, help="Delete and rebuild venv from scratch")
@click.option("--dsn", help="PostgreSQL DSN (default: SQLite)")
@click.option("--force-config", is_flag=True, help="Reset all config files to templates")
@click.pass_context
def bootstrap(ctx, with_brew, no_deps, no_venv_create, recreate_venv, dsn, force_config):
    """Bootstrap the platform (run once per machine)."""
    config = get_config()
    home = Path(config.get_claude_env_home())

    # Run the original bootstrap.py logic
    from claudenv.bootstrap import run_bootstrap
    run_bootstrap(
        with_brew=with_brew,
        no_deps=no_deps,
        no_venv_create=no_venv_create,
        recreate_venv=recreate_venv,
        dsn=dsn,
        force_config=force_config,
    )


@cli.command()
@click.argument("repo_root", type=click.Path(exists=True, file_okay=False, resolve_path=True))
@click.option("--repo-name", help="Repo slug for namespaces")
@click.option("--tier", type=click.Choice(["0", "1", "2", "3"]), help="Privacy tier")
@click.option("--description", default="", help="Short description")
@click.option("--branch", help="Default branch")
@click.option("--yes", "-y", is_flag=True, help="Non-interactive, accept defaults")
@click.option("--dry-run", is_flag=True, help="Print what would change")
@click.option("--no-template", is_flag=True, help="Skip CLAUDE.md and skills")
@click.option("--force-template", is_flag=True, help="Overwrite existing skills/agents")
@click.option("--force-policy", is_flag=True, help="Regenerate repo-policy.yaml")
@click.option("--no-post-commit", is_flag=True, help="Skip git hooks")
@click.pass_context
def onboard(ctx, repo_root, repo_name, tier, description, branch, yes, dry_run, no_template, force_template,
            force_policy, no_post_commit):
    """Onboard a repository to claude-env."""
    config = get_config()
    service = OnboardingService(config)

    result = service.onboard(
        repo_root=repo_root,
        slug=repo_name,
        tier=int(tier) if tier else None,
        branch=branch,
        description=description,
        dry_run=dry_run,
        force_policy=force_policy,
        force_template=force_template,
        no_template=no_template,
        no_post_commit=no_post_commit,
    )

    if dry_run:
        click.echo("DRY RUN - nothing written")
    else:
        click.echo(f"Onboarded: {result.slug} (tier {result.tier})")
        click.echo(f"  RAG table: {result.rag_table}")
        click.echo(f"  Memory ns: {result.memory_namespace} (isolated={result.memory_isolated})")


@cli.command()
@click.argument("repo_root", type=click.Path(exists=True, file_okay=False, resolve_path=True))
@click.pass_context
def scan(ctx, repo_root):
    """Preview what would be indexed (policy allow/block split)."""
    config = get_config()
    service = OnboardingService(config)

    # Load policy and simulate
    from claudenv.domain.policy import PolicyEngine
    engine = PolicyEngine.load(repo_root)

    # Get candidate files
    import subprocess
    try:
        out = subprocess.run(
            ["git", "-C", repo_root, "ls-files"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        files = out.splitlines() if out else []
    except Exception:
        files = [str(p.relative_to(repo_root)) for p in Path(repo_root).rglob("*") if p.is_file()]

    allowed, blocked = [], []
    for f in files:
        decision = engine.evaluate_path(f)
        (allowed if decision.is_allowed else blocked).append((f, decision.reason))

    click.echo(f"Candidates: {len(files)}")
    click.echo(f"Allowed: {len(allowed)}")
    click.echo(f"Blocked: {len(blocked)}")
    if blocked:
        click.echo("\nBlocked:")
        for path, reason in blocked[:20]:
            click.echo(f"  {path}: {reason}")
        if len(blocked) > 20:
            click.echo(f"  ... and {len(blocked) - 20} more")


@cli.command()
@click.argument("repo_root", type=click.Path(exists=True, file_okay=False, resolve_path=True))
@click.pass_context
def index(ctx, repo_root):
    """Build full RAG index for a repository."""
    config = get_config()
    container = get_container()

    # Get repo slug from onboarding
    repo_policy_path = Path(repo_root) / ".claude" / "repo-policy.yaml"
    if not repo_policy_path.exists():
        click.echo("ERROR: Repo not onboarded. Run 'claude-env onboard' first.", err=True)
        sys.exit(1)

    import yaml
    policy_data = yaml.safe_load(repo_policy_path.read_text())
    slug = policy_data.get("repo", Path(repo_root).name)
    branch = "main"  # TODO: detect

    service = container.get(RagService)

    # Get files
    import subprocess
    out = subprocess.run(
        ["git", "-C", repo_root, "ls-files"],
        capture_output=True, text=True, timeout=10,
    ).stdout.strip()
    files = out.splitlines() if out else []

    click.echo(f"Indexing {len(files)} files...")
    files_dict = {}
    for f in files:
        path = Path(repo_root) / f
        try:
            files_dict[f] = path.read_text(errors="ignore")
        except Exception:
            pass

    result = service.indexer.full_index(
        repo=slug, branch=branch, files=files_dict,
    )
    click.echo(f"Indexed: {result['files']} files, {result['chunks']} chunks")


@cli.group()
def validate():
    """Run validation checks."""
    pass


@validate.command("installation")
@click.pass_context
def validate_installation(ctx):
    """Validate full installation."""
    config = get_config()
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


@cli.command()
@click.argument("repo_root", type=click.Path(exists=True, file_okay=False, resolve_path=True))
@click.argument("query")
@click.pass_context
def rag(ctx, repo_root, query):
    """Test RAG retrieval for a repository."""
    config = get_config()
    container = get_container()

    repo_policy_path = Path(repo_root) / ".claude" / "repo-policy.yaml"
    import yaml
    policy_data = yaml.safe_load(repo_policy_path.read_text())
    slug = policy_data.get("repo", Path(repo_root).name)
    branch = "main"

    service = container.get(RagService)
    results = service.search(
        repo=slug, branch=branch, query=query, top_k=5,
    )

    if not results:
        click.echo("No results")
        return

    for r in results:
        click.echo(f"\n[{r.rank}] {r.chunk.file_path}:{r.chunk.start_line}-{r.chunk.end_line} (score: {r.score:.3f})")
        click.echo(f"    {r.chunk.text[:200]}...")


@cli.command()
@click.argument("repo_root", type=click.Path(exists=True, file_okay=False, resolve_path=True))
@click.pass_context
def hooks(ctx, repo_root):
    """Install native-tool governance hooks."""
    config = get_config()
    home = Path(config.get_claude_env_home())

    installer = home / "hooks" / "install_hooks.py"
    if not installer.exists():
        click.echo("ERROR: Hook installer not found", err=True)
        sys.exit(1)

    import subprocess
    result = subprocess.run([sys.executable, str(installer), "--repo", repo_root])
    sys.exit(result.returncode)


@cli.command()
@click.option("--window", default="7d", help="Time window (e.g., 7d, 30d)")
@click.option("--repo", help="Filter by repo")
@click.option("--format", type=click.Choice(["markdown", "csv"]), default="markdown")
@click.option("--out", type=click.Path(), help="Output file")
@click.pass_context
def report(ctx, window, repo, format, out):
    """Generate compliance report."""
    config = get_config()
    container = get_container()

    generator = ComplianceReportGenerator(container.get(IDatabase), container.get(IAuditRepository))

    result = generator.generate(window=window, repo=repo, format=format)

    if out:
        Path(out).write_text(result)
        click.echo(f"Report written to {out}")
    else:
        click.echo(result)


@cli.command()
@click.option("--list", "list_sessions", is_flag=True, help="List recent sessions")
@click.argument("session_id", required=False)
@click.pass_context
def replay(ctx, list_sessions, session_id):
    """Replay session forensics."""
    config = get_config()
    container = get_container()

    replay = SessionReplay(container.get(IDatabase))

    if list_sessions:
        sessions = replay.list_recent(20)
        for s in sessions:
            click.echo(f"  {s['session_id']}  {s['events']} events  {s['first']} -> {s['last']}  actors={s['actors']}")
    elif session_id:
        timeline = replay.get_timeline(session_id)
        for step in timeline:
            click.echo(f"  [{step['ts']}] #{step['event_id']} {step['type']} [{step['actor']}] {step['summary']}")
    else:
        click.echo("Use --list or provide a session_id")


@cli.command()
@click.argument("action", type=click.Choice(["on", "off", "status"]))
@click.option("--reason", default="", help="Reason for incident mode")
@click.option("--by", default="operator", help="Operator identity")
@click.pass_context
def incident(ctx, action, reason, by):
    """Incident mode kill switch."""
    config = get_config()
    home = Path(config.get_claude_env_home())
    marker = home / "state" / "INCIDENT"

    if action == "on":
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            f'{{"reason": "{reason}", "by": "{by}", "at": "{__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()}"}}')
        click.echo(f"Incident mode ON: {reason}")
    elif action == "off":
        if marker.exists():
            marker.unlink()
            click.echo("Incident mode OFF")
        else:
            click.echo("Incident mode was not active")
    else:
        if marker.exists():
            import json
            data = json.loads(marker.read_text())
            click.echo(f"Incident mode ACTIVE since {data.get('at')} by {data.get('by')}: {data.get('reason')}")
        else:
            click.echo("Incident mode OFF")


@cli.command()
@click.pass_context
def services(ctx):
    """List running local UI services."""
    config = get_config()
    home = Path(config.get_claude_env_home())
    registry_file = home / "state" / "services.json"

    if not registry_file.exists():
        click.echo("No services running")
        return

    import json
    data = json.loads(registry_file.read_text())
    for name, info in data.items():
        click.echo(f"  {name}: {info['url']} (pid={info.get('pid')})")


@cli.command()
@click.option("--repo", help="Filter to one repo")
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text")
@click.pass_context
def budget(ctx, repo, fmt):
    """Check month-to-date spend against configured budgets (advisory).

    Exits non-zero only when a budget is EXCEEDED, so a CI job or shell prompt
    can gate on it. Warnings alone still exit 0.
    """
    svc = get_container().get(BudgetService)
    result = svc.evaluate(repo=repo)

    if fmt == "json":
        import json
        click.echo(json.dumps(result.to_dict(), indent=2))
    else:
        if not result.repos:
            click.echo("No session cost data yet.")
        else:
            click.echo(f"{'repo':<24} {'sessions':>8} {'spent':>10} {'budget':>10} {'pct':>6}  status")
            for r in result.repos:
                budget_s = f"${r.budget_usd:.2f}" if r.budget_usd else "-"
                pct_s = f"{r.pct*100:.0f}%" if r.pct is not None else "-"
                click.echo(
                    f"{r.repo:<24} {r.sessions:>8} ${r.spent_usd:>8.2f} {budget_s:>10} "
                    f"{pct_s:>6}  {r.status.value}"
                )
        click.echo(f"\noverall: {result.overall.value}")

    if result.overall.value == "EXCEEDED":
        sys.exit(1)


@cli.command()
@click.option("--window", default="30d", help="Time window (e.g. 24h, 7d, 30d)")
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text")
@click.pass_context
def dashboard(ctx, window, fmt):
    """Read-only operational summary (costs, latency, retrieval quality, violations)."""
    svc = get_container().get(DashboardService)
    summary = svc.summary(window=window)

    if fmt == "json":
        import json
        click.echo(json.dumps(summary, indent=2, default=str))
        return

    click.echo(f"== claude-env dashboard (window={window}) ==\n")

    click.echo("Top session costs:")
    for row in summary["top_session_costs"]:
        click.echo(f"  {row['repo']:<20} ${row['spent_usd']:>8}  {row['session_id']}")

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


@cli.command()
@click.option("--repo", help="Filter to one repo")
@click.pass_context
def feedback(ctx, repo):
    """Show RAG retrieval feedback stats (retrieved/used counts, top files)."""
    svc = get_container().get(FeedbackService)
    stats = svc.stats(repo=repo)
    click.echo(f"retrieved: {stats.get('retrieved', 0)}")
    click.echo(f"used:      {stats.get('used', 0)}")
    top = stats.get("top_used_files", [])
    if top:
        click.echo("top used files:")
        for path, n in top:
            click.echo(f"  {n:>4}  {path}")


if __name__ == "__main__":
    cli()
