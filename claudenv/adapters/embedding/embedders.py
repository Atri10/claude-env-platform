"""
claude-env :: Adapters - Embedding backends

Merges the previously separate embedder_backend.py, llama_cpp_embedder.py,
and dummy_embedder.py modules into one themed module.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


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
            from llama_cpp.llama_cpp import (
                LLAMA_POOLING_TYPE_CLS,
                LLAMA_POOLING_TYPE_LAST,
                LLAMA_POOLING_TYPE_MEAN,
                LLAMA_POOLING_TYPE_NONE,
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


class OnnxEmbedder(EmbedderBackend):
    """ONNX Runtime embedding backend (sentence-transformers exported models).

    Requires onnxruntime (already a dependency). Tokenization uses the
    HuggingFace tokenizers library if installed, or falls back to a
    minimal whitespace tokenizer that produces usable (not perfect) results.
    """

    def __init__(
            self,
            model_dir: str,
            model_name: str = "onnx",
            embedding_dim: int = 384,
    ):
        try:
            import onnxruntime as ort
        except ImportError:
            raise RuntimeError("onnxruntime not installed")

        model_path = Path(model_dir)
        if model_path.is_file():
            model_path = model_path

        onnx_file = model_path / "model.onnx"
        if not onnx_file.exists():
            raise RuntimeError(f"ONNX model not found at {onnx_file}")

        self._session = ort.InferenceSession(str(onnx_file))
        self._model_name = model_name
        self._embedding_dim = embedding_dim
        self._model_dir = model_path

        input_names = [i.name for i in self._session.get_inputs()]
        output_name = self._session.get_outputs()[0].name
        self._input_names = input_names
        self._output_name = output_name
        inferred = self._infer_max_length(model_path)
        self._max_length = inferred or 2048

        # Try loading the tokenizer; fall back to basic whitespace
        tokenizer_path = model_path / "tokenizer.json"
        self._tokenizer = None
        if tokenizer_path.exists():
            try:
                from tokenizers import Tokenizer
                self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
                if self._max_length:
                    self._tokenizer.enable_truncation(self._max_length)
            except ImportError:
                logger.warning(
                    "ONNX tokenizer.json found but 'tokenizers' library not installed. "
                    "Install with: pip install tokenizers"
                )
            except Exception:
                logger.warning("failed to load tokenizer.json", exc_info=True)

        logger.info(
            "ONNX embedder loaded: %s dim=%d max_len=%d tokenizer=%s",
            self._model_name, self._embedding_dim, self._max_length or -1,
            "loaded" if self._tokenizer else "fallback-whitespace",
        )

    @staticmethod
    def _infer_max_length(model_dir: Path) -> int | None:
        """Read the model's max sequence length from config.json or model metadata."""
        config_path = model_dir / "config.json"
        if config_path.exists():
            try:
                import json
                cfg = json.loads(config_path.read_text())
                for key in ("max_position_embeddings", "n_positions", "max_seq_len", "seq_length"):
                    val = cfg.get(key)
                    if val and isinstance(val, int) and val > 0:
                        return val
            except Exception:
                pass
        return None

    @property
    def dim(self) -> int:
        return self._embedding_dim

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def backend_name(self) -> str:
        return "onnx"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        inputs = self._tokenize(text)
        outputs = self._session.run([self._output_name], inputs)
        vec = outputs[0][0]  # first (only) batch element
        if len(vec.shape) > 1:
            vec = vec.mean(axis=0)  # mean pool if token-level output
        norm = float(sum(x * x for x in vec) ** 0.5) or 1.0
        return [float(x / norm) for x in vec]

    def _tokenize(self, text: str) -> dict:
        if self._tokenizer is not None:
            encoded = self._tokenizer.encode(text)
            ids = encoded.ids[:self._max_length]
            mask = encoded.attention_mask[:self._max_length]
        else:
            ids = [min(ord(c), 30000) for c in text[:self._max_length]]
            mask = [1] * len(ids)

        result: dict = {
            "input_ids": [ids],
            "attention_mask": [mask],
        }
        if "token_type_ids" in self._input_names:
            result["token_type_ids"] = [[0] * len(ids)]
        return result
