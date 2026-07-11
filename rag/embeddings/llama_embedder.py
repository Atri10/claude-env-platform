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

Pooling: `embedding.pooling_type` ("mean" | "cls" | "last" | "none") tells llama.cpp
how to collapse a model's raw per-token output into one vector per input. Get this
wrong (or leave it "none" for a GGUF that needs pooling) and every embed call
returns one vector PER TOKEN instead of one per input — __init__ catches this at
construction time with a real embed of a throwaway string, so a misconfigured
model fails immediately with a fix-it message instead of partway through a
multi-hundred-file indexing run. See the `rag-model-setup` skill for how to probe
a new GGUF's correct pooling type before editing rag.yaml.
"""
from __future__ import annotations

import os
import struct
from pathlib import Path

from rag.embeddings.base import EmbedderBackend

try:
    from llama_cpp import Llama
    from llama_cpp.llama_cpp import (
        LLAMA_POOLING_TYPE_CLS, LLAMA_POOLING_TYPE_LAST,
        LLAMA_POOLING_TYPE_MEAN, LLAMA_POOLING_TYPE_NONE,
    )
except ImportError:  # pragma: no cover - allow import without binary installed
    Llama = None

# Maps the human-readable `embedding.pooling_type` string in rag.yaml to the
# llama.cpp enum. "none" means the model's own GGUF-declared default is used
# (some GGUFs bake in pooling; others don't, which is what causes unpooled
# per-token output when nothing is specified).
_POOLING_TYPES = {
    "mean": LLAMA_POOLING_TYPE_MEAN if Llama else None,
    "cls": LLAMA_POOLING_TYPE_CLS if Llama else None,
    "last": LLAMA_POOLING_TYPE_LAST if Llama else None,
    "none": LLAMA_POOLING_TYPE_NONE if Llama else None,
} if Llama else {}


class LlamaEmbedder(EmbedderBackend):
    backend_name = "llama_cpp"

    def __init__(self, model_path: str, model_name: str, embedding_dim: int,
                 n_ctx: int = 2048, n_gpu_layers: int = -1,
                 n_threads: int | None = None,
                 document_prefix: str = "", query_prefix: str = "",
                 pooling_type: str = "mean"):
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
        if pooling_type not in _POOLING_TYPES:
            raise ValueError(
                f"Unknown embedding.pooling_type '{pooling_type}'. "
                f"Valid values: {sorted(_POOLING_TYPES)}.")
        self._pooling_type_name = pooling_type
        self.llm = Llama(
            model_path=self._model_path,
            embedding=True,
            pooling_type=_POOLING_TYPES[pooling_type],
            n_ctx=n_ctx,
            n_threads=n_threads or os.cpu_count() or 8,
            n_gpu_layers=n_gpu_layers,
            verbose=False,
        )
        self._self_check()

    def _self_check(self) -> None:
        """Fail fast at construction, not partway through indexing hundreds of files.

        Embeds a throwaway string and checks (a) the output is a single pooled
        vector, not one-per-token, and (b) its length matches the configured
        embedding_dim. Both are the two ways a model/config mismatch shows up, and
        both are far cheaper to catch here (one embed call) than after chunking
        and embedding a large fraction of a repo.
        """
        vec = self._embed("claude-env pooling self-check")
        if len(vec) != self.dim:
            raise ValueError(
                f"Embedder self-check failed for '{self.model_name}': produced a "
                f"{len(vec)}-dim vector but embedding.embedding_dim in rag.yaml is "
                f"set to {self.dim}. Set embedding_dim to {len(vec)} (the model's "
                f"real output size), or double-check pooling_type='{self._pooling_type_name}' "
                f"is correct for this model -- different pooling types can also change "
                f"the effective output length for some architectures. See the "
                f"rag-model-setup skill for how to probe a model before configuring it.")

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
            pooling_type=getattr(cfg, "pooling_type", "mean"),
        )

    def _doc(self, t: str) -> str:
        return f"{self._doc_prefix}{t}"

    def _query(self, t: str) -> str:
        return f"{self._query_prefix}{t}"

    def _embed(self, text: str) -> list[float]:
        out = self.llm.create_embedding(text)
        vec = out["data"][0]["embedding"]
        if vec and isinstance(vec[0], list):
            raise TypeError(
                f"Embedding model returned {len(vec)} per-token vectors instead of one "
                f"pooled vector, for pooling_type='{self._pooling_type_name}'. Try a "
                f"different embedding.pooling_type in rag.yaml -- 'mean' works for most "
                f"embedding-tuned models, 'cls' or 'last' for some others; 'none' only "
                f"works if the GGUF itself bakes in pooling. See the rag-model-setup "
                f"skill for how to probe which one your model actually needs.")
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
