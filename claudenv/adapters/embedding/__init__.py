"""
claude-env :: Adapters - Embedding & Reranking
"""
from __future__ import annotations

from .embedder_backend import EmbedderBackend
from .llama_cpp_embedder import LlamaCppEmbedder
from .dummy_embedder import DummyEmbedder
from .reranker_backend import RerankerBackend
from .onnx_cross_encoder_reranker import OnnxCrossEncoderReranker
from .noop_reranker import NoopReranker
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
