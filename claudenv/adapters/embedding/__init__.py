"""
claude-env :: Adapters - Embedding & Reranking
"""
from __future__ import annotations

from .embedders import DummyEmbedder, EmbedderBackend, LlamaCppEmbedder
from .factory import (
    get_embedder,
    get_reranker,
    reload_embedder,
    reload_reranker,
)
from .rerankers import NoopReranker, OnnxCrossEncoderReranker, RerankerBackend

__all__ = [
    "EmbedderBackend",
    "LlamaCppEmbedder",
    "DummyEmbedder",
    "RerankerBackend",
    "OnnxCrossEncoderReranker",
    "NoopReranker",
    "get_embedder",
    "get_reranker",
    "reload_embedder",
    "reload_reranker",
]
