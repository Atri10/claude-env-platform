"""
claude-env :: embedding layer (llama.cpp)
File: rag/embeddings/llama_embedder.py

This module knows HOW to run a GGUF embedding model with llama.cpp. It does NOT
decide WHICH model to use, nor does it know any model's name — that is configured
manually at setup in config/rag.yaml (resolved by rag/config.py). Construct via
the factory so the model choice stays in config, not code:

    from rag.config import get_embedder
    emb = get_embedder()
    vecs = emb.embed_documents(["def f(): ..."])
    qv   = emb.embed_query("how is jwt validated")

Task-prefix handling: some models require a per-task prefix on the input text
(for example a document-vs-query prefix). Those prefixes are NOT hardcoded — set
`embedding.document_prefix` / `embedding.query_prefix` in config/rag.yaml (empty
by default = no prefix). The configured strings are prepended verbatim.
"""
from __future__ import annotations

import os
import struct
from pathlib import Path

try:
    from llama_cpp import Llama
except ImportError:  # pragma: no cover - allow import without binary installed
    Llama = None


class LlamaEmbedder:
    def __init__(self, model_path: str, model_name: str, embedding_dim: int,
                 n_ctx: int = 2048, n_gpu_layers: int = -1,
                 n_threads: int | None = None,
                 document_prefix: str = "", query_prefix: str = ""):
        self.dim = embedding_dim
        self._model_path = model_path
        self.model_name = model_name or Path(model_path).stem
        # Task prefixes come from config (empty = none). No model name is inspected.
        self._doc_prefix = document_prefix
        self._query_prefix = query_prefix
        if Llama is None:
            raise RuntimeError(
                "llama-cpp-python not installed. "
                "Install: CMAKE_ARGS='-DLLAMA_METAL=on' pip install llama-cpp-python")
        self.llm = Llama(
            model_path=self._model_path,
            embedding=True,
            n_ctx=n_ctx,
            n_threads=n_threads or os.cpu_count() or 8,
            n_gpu_layers=n_gpu_layers,
            verbose=False,
        )

    @classmethod
    def from_config(cls, cfg) -> "LlamaEmbedder":
        """Build from a rag.config.EmbeddingConfig (the only sanctioned path)."""
        return cls(
            model_path=cfg.model_path,
            model_name=cfg.model_name,
            embedding_dim=cfg.embedding_dim,
            n_ctx=cfg.n_ctx,
            n_gpu_layers=cfg.n_gpu_layers,
            document_prefix=cfg.document_prefix,
            query_prefix=cfg.query_prefix,
        )

    def _doc(self, t: str) -> str:
        return f"{self._doc_prefix}{t}"

    def _query(self, t: str) -> str:
        return f"{self._query_prefix}{t}"

    def _embed(self, text: str) -> list[float]:
        out = self.llm.create_embedding(text)
        vec = out["data"][0]["embedding"]
        # L2 normalize for cosine == dot
        norm = sum(x * x for x in vec) ** 0.5 or 1.0
        return [x / norm for x in vec]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(self._doc(t)) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(self._query(text))

    # helpers for storing raw float32 in SQLite BLOB if needed
    @staticmethod
    def to_blob(vec: list[float]) -> bytes:
        return struct.pack(f"<{len(vec)}f", *vec)

    @staticmethod
    def from_blob(blob: bytes) -> list[float]:
        n = len(blob) // 4
        return list(struct.unpack(f"<{n}f", blob))
