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
@click.option("--skip-reranker", is_flag=True, help="Only download the embedding model.")
@click.pass_context
def model_ensure(ctx, yes, skip_reranker):
    """Download and configure recommended models (one-click setup).

    Downloads both the embedding model and reranker by default, configures
    rag.yaml, and shows progress. Skips models already configured on disk.
    """
    from claudenv.application.model_manager import ModelManager

    mgr = ModelManager()
    status = mgr.status()

    if status.configured:
        click.echo(f"Embedding model already configured: {status.model_path}")
        click.echo(f"  backend: {status.backend}, dimension: {status.dim}")
        return

    click.echo("Recommended models:")
    click.echo("  Embedding: all-MiniLM-L6-v2 (384-dim ONNX, ~90 MB)")
    if not skip_reranker:
        click.echo("  Reranker:  ms-marco-MiniLM-L-6-v2 (cross-encoder ONNX, ~90 MB)")
    click.echo(f"  Total:     ~{'90' if skip_reranker else '180'} MB")
    click.echo()

    if not yes and not click.confirm("Download and configure now?", default=True):
        click.echo("Skipped. Configure manually: claude-env model setup")
        return

    all_ok = True

    # --- Embedding model ---
    click.echo("\nEmbedding model:")
    bar_state = {}

    def _mk_progress(label):
        def _cb(downloaded, total):
            if "bar" not in bar_state:
                bar_state["bar"] = click.progressbar(length=total, label=label)
                bar_state["bar"].__enter__()
            bar_state["bar"].update(downloaded - bar_state.get("last", 0))
            bar_state["last"] = downloaded
        return _cb

    result = mgr.ensure_model("embedding", progress_callback=_mk_progress("  model.onnx"))
    if "bar" in bar_state:
        bar_state["bar"].render_finish()

    if result.success:
        click.echo(f"  Configured at {result.model_path}")
        click.echo(f"  Size: {result.size_bytes / 1024 / 1024:.1f} MB")
    else:
        click.echo(f"  FAILED: {result.message}", err=True)
        all_ok = False

    # --- Reranker ---
    if not skip_reranker:
        click.echo("\nReranker model:")
        bar_state.clear()

        reranker_result = mgr.ensure_model("reranker", progress_callback=_mk_progress("  model.onnx"))
        if "bar" in bar_state:
            bar_state["bar"].render_finish()

        if reranker_result.success:
            click.echo(f"  Configured at {reranker_result.model_path}")
            click.echo(f"  Size: {reranker_result.size_bytes / 1024 / 1024:.1f} MB")
        else:
            click.echo(f"  {reranker_result.message} (RAG works without reranker)", err=True)

    click.echo()
    if all_ok:
        click.echo("Done. Ready to index! Run: claude-env index <repo>")
    else:
        click.echo("Some downloads failed. Retry with claude-env model ensure.")
        ctx.exit(1)
