"""
claude-env :: CLI Entry Point
"""
from __future__ import annotations

import click
import os
import sys
from pathlib import Path

from claudenv.adapters.config import get_config
from claudenv.application.onboarding import OnboardingService
from claudenv.di import get_container


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

    from claudenv.application.rag import RagService
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

    from claudenv.application.audit import ComplianceReportGenerator
    generator = ComplianceReportGenerator(container.get(IAuditRepository))

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

    from claudenv.application.audit import SessionReplay
    replay = SessionReplay(container.get(IAuditRepository))

    if list_sessions:
        sessions = replay.list_recent(20)
        for s in sessions:
            click.echo(f"  {s['session_id']}  {s['repo']}  {s['started']}  {s['actions']} actions")
    elif session_id:
        timeline = replay.get_timeline(session_id)
        for step in timeline:
            click.echo(f"  [{step['ts']}] {step['actor']}: {step['action']} -> {step['result']}")
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


if __name__ == "__main__":
    cli()
