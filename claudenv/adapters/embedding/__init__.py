"""
claude-env :: Adapters - Embedding & Reranking
"""
from __future__ import annotations

from .embedders import EmbedderBackend, LlamaCppEmbedder, DummyEmbedder
from .rerankers import RerankerBackend, OnnxCrossEncoderReranker, NoopReranker
from .factory import (
    get_embedder,
    get_reranker,
    reload_embedder,
    reload_reranker,
)

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
