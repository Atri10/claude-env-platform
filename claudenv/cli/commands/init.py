"""claude-env :: CLI - init command."""
from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path

import click
import yaml

from claudenv._data import config_dir, sql_dir
from claudenv.adapters.audit import SqliteAuditLogger
from claudenv.adapters.config import get_config
from claudenv.adapters.persistence import SQLiteDatabase
from claudenv.cli.context import _model_setup_flow
from claudenv.domain.value_objects import SessionId

logger = logging.getLogger(__name__)


@click.command("init")
@click.option("--force-config", is_flag=True,
              help="Overwrite existing deployed config files with the packaged defaults.")
@click.option("--interactive", "-i", is_flag=True,
              help="After initializing, run interactive model setup (embedding + reranker).")
@click.pass_context
def init(ctx, force_config, interactive):
    """Initialize the local claude-env home (one-time, idempotent).

    Creates $CLAUDE_ENV_HOME (default ~/.claude-env), applies the SQL schema,
    copies the packaged default config, and writes the genesis audit event.
    """
    config = get_config()
    home = Path(config.get_claude_env_home())

    logger.debug("[flow] init: start home=%s force_config=%s interactive=%s", home, force_config, interactive)
    verbose = ctx.obj.get("verbose", False) if ctx.obj else False

    for sub in ("state", "config", "knowledge/lancedb"):
        (home / sub).mkdir(parents=True, exist_ok=True)
    click.echo(f"  [ok] home ready at {home}")
    logger.debug("[flow] init: directory skeleton created")

    copied, skipped = 0, 0
    for src in sorted(config_dir().glob("*")):
        if not src.is_file():
            continue
        dst = home / "config" / src.name
        if dst.exists() and not force_config:
            skipped += 1
            if verbose:
                click.echo(f"       skip {src.name} (exists)")
            continue
        shutil.copy2(src, dst)
        copied += 1
        if verbose:
            click.echo(f"       copy {src.name}")

    click.echo(f"  [ok] config: {copied} copied, {skipped} kept")
    logger.debug("[flow] init: config copied=%d skipped=%d", copied, skipped)

    dsn = config.get_database_dsn()
    db = SQLiteDatabase(dsn)
    db.apply_schema(str(sql_dir() / "schema.sql"))
    click.echo(f"  [ok] schema applied -> {dsn}")

    audit_logger = SqliteAuditLogger(
        db=db, session_id=SessionId.from_string("init"), actor="claude-env-init",
    )
    audit_logger.agent_action(
        agent="claude-env-init", action="init", target=str(home),
        summary="claude-env home initialized",
    )
    result = audit_logger.verify_chain()
    db.close()
    ok = result.ok

    click.echo(f"  [ok] audit ledger initialized (verify_chain: {'green' if ok else 'FAILED'})")
    logger.debug("[flow] init: audit ledger initialized ok=%s", ok)

    if not ok:
        click.echo("\nInitialization completed but the audit chain did not verify!", err=True)
        sys.exit(1)

    click.echo("\nclaude-env is ready.")
    rag_cfg = Path(home) / "config" / "rag.yaml"
    if rag_cfg.exists():
        cfg_data = yaml.safe_load(rag_cfg.read_text()) or {}
        model_path = cfg_data.get("embedding", {}).get("model_path", "")
        if not model_path:
            click.echo(
                "\n  No embedding model configured yet.\n"
                "  Quick start:  claude-env model ensure  (one-click recommended setup)\n"
                "  Manual:       claude-env model setup   (choose your own model)\n"
                "\n  Next:         claude-env onboard <repo>"
            )
        else:
            click.echo("\n  Next: claude-env onboard <repo>")
    else:
        click.echo("\n  Next: claude-env onboard <repo>")

    if interactive:
        click.echo("\n=== Model setup ===")
        _model_setup_flow(
            ctx, interactive=sys.stdin.isatty(),
            model_path=None, pooling_type=None,
            embedding_dim=None, reranker_dir=None,
        )
