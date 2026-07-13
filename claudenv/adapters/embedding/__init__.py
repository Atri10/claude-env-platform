"""
claude-env :: Adapters - Embedding & Reranking
"""
from __future__ import annotations

import json
import os
import struct
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claudenv.domain.rag import RAGConfig


# ============================================================================
# Embedding Backends (Strategy Pattern)
# ============================================================================

class EmbedderBackend(ABC):
    """Base class for embedding backends."""

    @property
    @abstractmethod
    def dim(self) -> int:
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        ...

    @property
    @abstractmethod
    def backend_name(self) -> str:
        ...

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        ...

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        ...

    def info(self) -> dict[str, Any]:
        return {"backend": self.backend_name, "model_name": self.model_name, "dim": self.dim}


class LlamaCppEmbedder(EmbedderBackend):
    """llama.cpp embedding backend."""

    def __init__(
            self,
            model_path: str,
            model_name: str,
            n_ctx: int,
            n_gpu_layers: int,
            embedding_dim: int,
            document_prefix: str = "",
            query_prefix: str = "",
            pooling_type: str = "mean",
    ):
        try:
            from llama_cpp import Llama
        except ImportError:
            raise RuntimeError("llama-cpp-python not installed")

        self._model = Llama(
            model_path=model_path,
            n_ctx=n_ctx,
            n_gpu_layers=n_gpu_layers,
            embedding=True,
            verbose=False,
        )
        self._model_name = model_name
        self._embedding_dim = embedding_dim
        self._doc_prefix = document_prefix
        self._query_prefix = query_prefix
        self._pooling = pooling_type

    @property
    def dim(self) -> int:
        return self._embedding_dim

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def backend_name(self) -> str:
        return "llama_cpp"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        prefixed = [self._doc_prefix + t for t in texts]
        return [self._embed_one(t) for t in prefixed]

    def embed_query(self, text: str) -> list[float]:
        return self._embed_one(self._query_prefix + text)

    def _embed_one(self, text: str) -> list[float]:
        # llama.cpp returns list of vectors for batch, single vector for single
        result = self._model.embed(text)
        if isinstance(result[0], list):
            return result[0]
        return result


class DummyEmbedder(EmbedderBackend):
    """Dummy embedder for testing (returns zero vectors)."""

    def __init__(self, dim: int = 768):
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return "dummy"

    @property
    def backend_name(self) -> str:
        return "dummy"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * self._dim for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.0] * self._dim


# ============================================================================
# Reranker Backends
# ============================================================================

class RerankerBackend(ABC):
    """Base class for reranker backends."""

    @abstractmethod
    def rerank(self, query: str, docs: list[str], top_k: int) -> list[int]:
        """Return indices of top_k docs after reranking."""
        ...

    def status(self) -> dict[str, Any]:
        return {"ok": True, "backend": self.__class__.__name__}


class OnnxCrossEncoderReranker(RerankerBackend):
    """ONNX Runtime cross-encoder reranker."""

    def __init__(self, model_dir: str):
        try:
            import onnxruntime as ort
            import numpy as np
        except ImportError:
            raise RuntimeError("onnxruntime not installed")

        model_path = Path(model_dir) / "model.onnx"
        if not model_path.exists():
            raise FileNotFoundError(f"ONNX model not found: {model_path}")

        self.session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        self.input_names = [i.name for i in self.session.get_inputs()]

    def rerank(self, query: str, docs: list[str], top_k: int) -> list[int]:
        import numpy as np

        # Prepare inputs (query, doc) pairs
        inputs = {}
        for name in self.input_names:
            if "query" in name.lower():
                inputs[name] = np.array([query] * len(docs), dtype=object)
            elif "doc" in name.lower() or "passage" in name.lower() or "text" in name.lower():
                inputs[name] = np.array(docs, dtype=object)

        scores = self.session.run(None, inputs)[0].flatten()
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return ranked[:top_k]

    def status(self) -> dict[str, Any]:
        return {"ok": True, "backend": "onnx_cross_encoder"}


class NoopReranker(RerankerBackend):
    """No-op reranker (disabled)."""

    def rerank(self, query: str, docs: list[str], top_k: int) -> list[int]:
        return list(range(min(top_k, len(docs))))

    def status(self) -> dict[str, Any]:
        return {"ok": False, "backend": "disabled"}


# ============================================================================
# Factories & Caching
# ============================================================================

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
