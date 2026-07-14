"""
claude-env :: Adapters - Embedding & Reranking - Factories & Caching
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from claudenv.domain.rag import RAGConfig

from .embedders import EmbedderBackend, LlamaCppEmbedder, DummyEmbedder
from .rerankers import RerankerBackend, OnnxCrossEncoderReranker, NoopReranker

_EMBEDDER: EmbedderBackend | None = None
_EMBEDDER_KEY: tuple | None = None
_RERANKER: RerankerBackend | None = None
_RERANKER_KEY: tuple | None = None
_LOCK = threading.Lock()


def get_embedder(cfg: RAGConfig | None = None) -> EmbedderBackend:
    """Get cached embedder instance."""
    global _EMBEDDER, _EMBEDDER_KEY

    if cfg is None:
        from claudenv.adapters.config import get_config
        cfg = get_config().get_rag_config()

    key = (cfg.embedding_backend, cfg.embedding_model_path)
    with _LOCK:
        if _EMBEDDER is None or _EMBEDDER_KEY != key:
            _EMBEDDER = _create_embedder(cfg)
            _EMBEDDER_KEY = key
        return _EMBEDDER


def _create_embedder(cfg: RAGConfig) -> EmbedderBackend:
    if cfg.embedding_backend == "llama_cpp":
        if not cfg.embedding_model_path:
            raise RuntimeError("No embedding model configured. Set embedding.model_path in config.")
        return LlamaCppEmbedder(
            model_path=os.path.expanduser(cfg.embedding_model_path),
            model_name=cfg.embedding_model_name or Path(cfg.embedding_model_path).stem,
            n_ctx=cfg.embedding_n_ctx,
            n_gpu_layers=cfg.embedding_n_gpu_layers,
            embedding_dim=cfg.embedding_dim,
            document_prefix=cfg.embedding_document_prefix,
            query_prefix=cfg.embedding_query_prefix,
            pooling_type=cfg.embedding_pooling_type,
        )
    elif cfg.embedding_backend == "dummy":
        return DummyEmbedder(dim=cfg.embedding_dim)
    else:
        raise ValueError(f"Unknown embedding backend: {cfg.embedding_backend}")


def get_reranker(cfg: RAGConfig | None = None) -> RerankerBackend:
    """Get cached reranker instance."""
    global _RERANKER, _RERANKER_KEY

    if cfg is None:
        from claudenv.adapters.config import get_config
        cfg = get_config().get_rag_config()

    key = (cfg.reranker_backend, cfg.reranker_model_dir)
    with _LOCK:
        if _RERANKER is None or _RERANKER_KEY != key:
            _RERANKER = _create_reranker(cfg)
            _RERANKER_KEY = key
        return _RERANKER


def _create_reranker(cfg: RAGConfig) -> RerankerBackend:
    if not cfg.reranker_model_dir:
        return NoopReranker()
    if cfg.reranker_backend == "onnx_cross_encoder":
        try:
            return OnnxCrossEncoderReranker(cfg.reranker_model_dir)
        except Exception:
            return NoopReranker()
    return NoopReranker()


def reload_embedder() -> None:
    global _EMBEDDER, _EMBEDDER_KEY
    with _LOCK:
        _EMBEDDER = None
        _EMBEDDER_KEY = None


def reload_reranker() -> None:
    global _RERANKER, _RERANKER_KEY
    with _LOCK:
        _RERANKER = None
        _RERANKER_KEY = None
