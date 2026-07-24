"""
claude-env :: CLI - Shared context and helpers.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

import click
import yaml

from claudenv._data import config_dir
from claudenv.adapters.config import get_config
from claudenv.adapters.config.providers import write_rag_config
from claudenv.di import get_container

logger = logging.getLogger(__name__)


def _dsn_to_db_path(dsn: str) -> Path:
    if dsn.startswith("sqlite:///"):
        return Path(dsn[len("sqlite:///"):])
    if dsn.startswith("sqlite://"):
        return Path(dsn[len("sqlite://"):])
    return Path(dsn)


def _is_initialized() -> bool:
    config = get_config()
    db_path = _dsn_to_db_path(config.get_database_dsn())
    return db_path.exists()


def ensure_initialized(ctx):
    if _is_initialized():
        return
    if click.confirm("claude-env is not initialized. Run 'claude-env init' now?", default=True):
        from claudenv.cli.commands.init import init
        ctx.invoke(init, force_config=False)
        logger.debug("[flow] ensure_initialized: invoking init")
    else:
        click.echo(
            "Aborting: claude-env home is not initialized.\n"
            "Run `claude-env init` first, then retry this command.",
            err=True,
        )
        ctx.exit(1)


def _detect_branch(repo_root: str) -> str:
    policy = Path(repo_root) / ".claude" / "repo-policy.yaml"
    if policy.exists():
        try:
            data = yaml.safe_load(policy.read_text()) or {}
            branch = data.get("default_branch")
            if branch:
                return str(branch)
        except Exception:
            logger.warning("branch detection failed; defaulting to main", exc_info=True)
    try:
        out = subprocess.run(
            ["git", "-C", repo_root, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        if out:
            return out
    except Exception:
        logger.warning("git branch listing failed; falling back to rglob", exc_info=True)
    return "main"


def _rag_service(repo: str, branch: str):
    from claudenv.adapters.vector.lancedb import LanceDbRagRetriever, LanceDbVectorStore
    from claudenv.application.rag import RagIndexer, RagService
    from claudenv.domain.rag import BranchName, RepoSlug
    from claudenv.ports import (
        IEmbeddingProvider,
        ILanceDBConfig,
        IRagBookkeeping,
        IRAGConfig,
        IReranker,
    )

    logger.debug("[flow] _rag_service: building repo=%s branch=%s", repo, branch)
    container = get_container()
    lancedb_cfg = container.get(ILanceDBConfig)
    rag_cfg = container.get(IRAGConfig)
    rcfg = rag_cfg.get_rag_config()
    store = LanceDbVectorStore(lancedb_cfg.get_lancedb_path(), rcfg.embedding_dim)
    embedder = container.get(IEmbeddingProvider)
    reranker = container.get(IReranker)
    bk = container.get(IRagBookkeeping)
    indexer = RagIndexer(
        RepoSlug.from_string(repo), BranchName.from_string(branch),
        store, bk, embedder, rcfg,
    )
    retriever = LanceDbRagRetriever(store, embedder, reranker)
    logger.debug("[flow] _rag_service: built RagService")
    return RagService(indexer, retriever, embedder, reranker, bookkeeping=bk)


def _model_setup_flow(ctx, *, interactive, model_path, pooling_type, embedding_dim, reranker_dir):
    config = get_config()
    logger.debug("[flow] _model_setup_flow: start interactive=%s", interactive)
    home = config.get_claude_env_home()
    deployed = Path(home) / "config" / "rag.yaml"
    if deployed.exists():
        base = yaml.safe_load(deployed.read_text()) or {}
    else:
        base = yaml.safe_load((config_dir() / "rag.yaml").read_text()) or {}
    base.setdefault("embedding", {})
    base.setdefault("reranker", {})
    emb = base["embedding"]
    rer = base["reranker"]

    if model_path is None:
        current = emb.get("model_path", "")
        if interactive:
            model_path = click.prompt(
                "Embedding model path (GGUF, empty to skip)",
                default=current, show_default=False,
            )
        else:
            model_path = current
    if model_path:
        p = Path(os.path.expanduser(str(model_path)))
        if not p.exists():
            if interactive:
                click.echo(f"  WARNING: model path does not exist: {p}")
                if not click.confirm("  Continue anyway?", default=False):
                    ctx.exit(1)
            else:
                click.echo(f"ERROR: embedding model path does not exist: {p}", err=True)
                ctx.exit(1)

    if pooling_type is None:
        if interactive:
            pooling_type = click.prompt(
                "Pooling type", default=emb.get("pooling_type", "mean"),
                type=click.Choice(["mean", "cls", "last", "none"]), show_default=True,
            )
        else:
            pooling_type = emb.get("pooling_type", "mean")

    if embedding_dim is None:
        if interactive:
            embedding_dim = click.prompt(
                "Embedding dimension", default=int(emb.get("embedding_dim", 768)),
                type=int, show_default=True,
            )
        else:
            embedding_dim = int(emb.get("embedding_dim", 768))

    if reranker_dir is None:
        if interactive:
            reranker_dir = click.prompt(
                "Reranker ONNX dir (optional, empty to disable)",
                default=rer.get("model_dir", ""), show_default=False,
            )
        else:
            reranker_dir = rer.get("model_dir", "")

    emb["model_path"] = str(model_path) if model_path else ""
    emb["pooling_type"] = pooling_type
    emb["embedding_dim"] = int(embedding_dim)
    rer["model_dir"] = str(reranker_dir) if reranker_dir else ""

    path = write_rag_config(home, base)
    logger.debug("[flow] _model_setup_flow: wrote config path=%s", path)
    click.echo(f"  Wrote RAG config -> {path}")
    return path
