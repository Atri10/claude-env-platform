"""claude-env :: CLI - model commands."""
from __future__ import annotations

import logging
import sys

import click

from claudenv.cli.context import _model_setup_flow

logger = logging.getLogger(__name__)


@click.group("model")
def model():
    """Model configuration (embedding + reranker)."""
    logger.debug("[flow] group: model")


@model.command("setup")
@click.option("--model-path", default=None,
              help="Path to a local GGUF embedding model. Empty/skip to leave unconfigured.")
@click.option("--pooling-type", default=None,
              type=click.Choice(["mean", "cls", "last", "none"]),
              help="Pooling type for the embedding model.")
@click.option("--embedding-dim", default=None, type=int,
              help="Embedding vector dimension.")
@click.option("--reranker-dir", default=None,
              help="Directory with an ONNX cross-encoder reranker (empty to disable).")
@click.option("--yes", "-y", is_flag=True,
              help="Non-interactive: accept defaults / provided flags (for CI).")
@click.pass_context
def model_setup(ctx, model_path, pooling_type, embedding_dim, reranker_dir, yes):
    """Configure the embedding + reranker models (writes deployed rag.yaml)."""
    interactive = (not yes) and sys.stdin.isatty()
    logger.debug("[flow] model setup: start interactive=%s", interactive)
    _model_setup_flow(
        ctx, interactive=interactive,
        model_path=model_path, pooling_type=pooling_type,
        embedding_dim=embedding_dim, reranker_dir=reranker_dir,
    )


@model.command("ensure")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
@click.pass_context
def model_ensure(ctx, yes):
    """Ensure an embedding model is configured (one-click recommended setup)."""
    from claudenv.application.model_manager import ModelManager

    mgr = ModelManager()
    status = mgr.status()

    if status.configured:
        click.echo(f"Embedding model already configured: {status.model_path}")
        click.echo(f"  backend: {status.backend}, dimension: {status.dim}")
        return

    click.echo("No embedding model configured.")
    click.echo()
    if not yes and not click.confirm(
        "Download and configure the recommended default model (all-MiniLM-L6-v2, ~90 MB)?",
        default=True,
    ):
        click.echo("Skipped. Configure manually: claude-env model setup")
        return

    click.echo("Downloading model...")
    result = mgr.ensure_model("embedding")
    if result.success:
        click.echo(f"Model configured: {result.model_path}")
        click.echo(f"Downloaded: {result.size_bytes / 1024 / 1024:.1f} MB")
        click.echo("\nReady to index! Run: claude-env index <repo>")
    else:
        click.echo(f"Failed: {result.message}", err=True)
        ctx.exit(1)
