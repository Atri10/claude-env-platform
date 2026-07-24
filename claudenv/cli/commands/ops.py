"""claude-env :: CLI - hooks, report, replay, incident, services commands."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import click

from claudenv.adapters.config import get_config
from claudenv.application.audit import ComplianceReportGenerator, SessionReplay
from claudenv.di import get_container
from claudenv.domain.value_objects import SessionId
from claudenv.ports.audit import IAuditRepository
from claudenv.ports.database import IDatabase

logger = logging.getLogger(__name__)


@click.command("hooks")
@click.argument("repo_root", type=click.Path(exists=True, file_okay=False, resolve_path=True))
@click.pass_context
def hooks(ctx, repo_root):
    """Install native-tool governance hooks."""
    from claudenv.adapters.hooks import HookInstaller
    logger.debug("[flow] hooks: install repo_root=%s", repo_root)
    result = HookInstaller(repo_root).install()
    click.echo(json.dumps(result, indent=2))
    logger.debug("[flow] hooks: installed=%s", result.get("installed"))
    if not result.get("installed"):
        sys.exit(1)


@click.command("report")
@click.option("--window", default="7d", help="Time window (e.g., 7d, 30d)")
@click.option("--repo", help="Filter by repo")
@click.option("--format", type=click.Choice(["markdown", "csv"]), default="markdown")
@click.option("--out", type=click.Path(), help="Output file")
@click.pass_context
def report(ctx, window, repo, format, out):
    """Generate compliance report."""
    container = get_container()
    logger.debug("[flow] report: start window=%s repo=%s format=%s out=%s", window, repo, format, out)
    generator = ComplianceReportGenerator(container.get(IDatabase), container.get(IAuditRepository))
    result = generator.generate(window=window, repo=repo, format=format)
    logger.debug("[flow] report: generated")
    if out:
        Path(out).write_text(result)
        click.echo(f"Report written to {out}")
    else:
        click.echo(result)


@click.command("replay")
@click.option("--list", "list_sessions", is_flag=True, help="List recent sessions")
@click.argument("session_id", required=False)
@click.pass_context
def replay(ctx, list_sessions, session_id):
    """Replay session forensics."""
    container = get_container()
    logger.debug("[flow] replay: start list_sessions=%s session_id=%s", list_sessions, session_id)
    replay_svc = SessionReplay(container.get(IDatabase))
    if list_sessions:
        sessions = replay_svc.list_recent(20)
        for s in sessions:
            click.echo(f"  {s['session_id']}  {s['events']} events  {s['first']} -> {s['last']}  actors={s['actors']}")
    elif session_id:
        timeline = replay_svc.get_timeline(session_id)
        for step in timeline:
            click.echo(f"  [{step['ts']}] #{step['event_id']} {step['type']} [{step['actor']}] {step['summary']}")
    else:
        click.echo("Use --list or provide a session_id")


@click.command("incident")
@click.argument("action", type=click.Choice(["on", "off", "status"]))
@click.option("--reason", default="", help="Reason for incident mode")
@click.option("--by", default="operator", help="Operator identity")
@click.pass_context
def incident(ctx, action, reason, by):
    """Incident mode kill switch."""
    from claudenv.domain.incident import IncidentState, clear_incident, write_incident
    logger.debug("[flow] incident: action=%s reason=%s by=%s", action, reason, by)

    config = get_config()
    home = Path(config.get_claude_env_home())

    if action == "on":
        try:
            from claudenv.adapters.audit import SqliteAuditLogger
            from claudenv.adapters.persistence import SQLiteDatabase
            from claudenv.application.approval import ApprovalGate

            db = SQLiteDatabase(config.get_database_dsn())
            audit = SqliteAuditLogger(
                db, SessionId.from_string("incident-mode"), actor="incident-mode",
                repo="", tier=None,
            )
            gate = ApprovalGate(audit, db)
            for row in gate.list_open():
                gate.resolve(row["request_id"], approved=False, decided_by="INCIDENT")
            db.close()
        except Exception:
            logger.warning("incident gate unavailable; arming incident mode anyway")
        write_incident(reason=reason, by=by, home=home)
        logger.debug("[flow] incident: ON armed reason=%s by=%s", reason, by)
        click.echo(f"Incident mode ON: {reason}")
    elif action == "off":
        clear_incident(home=home)
        logger.debug("[flow] incident: OFF cleared")
        click.echo("Incident mode OFF")
    else:
        state = IncidentState.read(home=home)
        if state.active:
            click.echo(f"Incident mode ACTIVE since {state.since} by {state.by}: {state.reason}")
        else:
            click.echo("Incident mode OFF")


@click.command("services")
@click.pass_context
def services(ctx):
    """List running local UI services."""
    config = get_config()
    home = Path(config.get_claude_env_home())
    logger.debug("[flow] services: list")
    registry_file = home / "state" / "services.json"
    if not registry_file.exists():
        click.echo("No services running")
        return
    data = json.loads(registry_file.read_text())
    for name, info in data.items():
        click.echo(f"  {name}: {info['url']} (pid={info.get('pid')})")
