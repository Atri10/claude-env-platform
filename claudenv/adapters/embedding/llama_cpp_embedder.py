"""
claude-env :: Adapters - Embedding - llama.cpp backend
"""
from __future__ import annotations

from .embedder_backend import EmbedderBackend


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
            from llama_cpp.llama_cpp import (
                LLAMA_POOLING_TYPE_CLS, LLAMA_POOLING_TYPE_LAST,
                LLAMA_POOLING_TYPE_MEAN, LLAMA_POOLING_TYPE_NONE,
            )
        except ImportError:
            raise RuntimeError("llama-cpp-python not installed")

        pooling_types = {
            "mean": LLAMA_POOLING_TYPE_MEAN,
            "cls": LLAMA_POOLING_TYPE_CLS,
            "last": LLAMA_POOLING_TYPE_LAST,
            "none": LLAMA_POOLING_TYPE_NONE,
        }
        if pooling_type not in pooling_types:
            raise ValueError(
                f"Unknown embedding.pooling_type '{pooling_type}'. "
                f"Valid values: {sorted(pooling_types)}."
            )

        # pooling_type MUST be passed to llama.cpp itself: without it the
        # model falls back to its own GGUF-declared default (often "none"),
        # which returns one raw vector PER TOKEN instead of one pooled vector
        # per input -- silently breaking every embed call downstream.
        self._model = Llama(
            model_path=model_path,
            n_ctx=n_ctx,
            n_gpu_layers=n_gpu_layers,
            embedding=True,
            pooling_type=pooling_types[pooling_type],
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
        vec = result[0] if (result and isinstance(result[0], list)) else result
        if vec and isinstance(vec[0], list):
            raise TypeError(
                f"Embedding model returned {len(vec)} per-token vectors instead of one "
                f"pooled vector, for pooling_type='{self._pooling}'. Try a different "
                f"embedding.pooling_type in rag.yaml -- 'mean' works for most "
                f"embedding-tuned models, 'cls' or 'last' for some others; 'none' only "
                f"works if the GGUF itself bakes in pooling."
            )
        # L2 normalize so cosine similarity reduces to a dot product.
        norm = sum(x * x for x in vec) ** 0.5 or 1.0
        return [x / norm for x in vec]
