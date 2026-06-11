"""
claude-env :: embedding layer (llama.cpp)
File: rag/embeddings/llama_embedder.py

MODEL / PARAMETER RECOMMENDATIONS (with reasoning)
--------------------------------------------------
1. Embedding model:   nomic-embed-text-v1.5 (GGUF, Q8_0)
   Reasoning: 768-dim, strong retrieval quality, fully local, Apple-Silicon
   friendly via llama.cpp Metal backend. Outperforms all-MiniLM on code-ish text
   while staying small (~140MB at Q8_0). Avoids any network egress (privacy).
   Alternative for code-heavy repos: nomic-embed-code or bge-code; heavier.

2. Context size (n_ctx): 2048
   Reasoning: our max chunk is ~512 tokens; 2048 gives headroom for the model's
   instruction prefix ("search_document:" / "search_query:") plus batching,
   without wasting KV cache memory.

3. Quantization: Q8_0 for the embedding model.
   Reasoning: embeddings are sensitive to quantization noise; Q8_0 keeps recall
   near-fp16 while halving memory. Do NOT use Q4 for embeddings -- retrieval
   quality degrades measurably. (Q4/Q5 are fine for *generation* LLMs.)

4. Chunk sizing: 512 tokens target (code), 384 (markdown), 256 (ADR/RFC sections).
   Reasoning: matches function-sized units; large enough for semantic coherence,
   small enough that one chunk is one idea -> sharper embeddings + better recall.

5. Chunk overlap: 64 tokens (code), 48 (markdown), 32 (sections).
   Reasoning: ~12% overlap preserves cross-boundary context (a call that spans a
   chunk edge) without inflating the index or double-counting in scoring.

Nomic requires task prefixes:
    documents -> "search_document: <text>"
    queries   -> "search_query: <text>"
This module applies them automatically.

Usage:
    emb = LlamaEmbedder()                  # reads config/rag.yaml
    vecs = emb.embed_documents(["def f(): ..."])
    qv   = emb.embed_query("how is jwt validated")
"""
from __future__ import annotations

import os
import struct
from pathlib import Path

try:
    from llama_cpp import Llama
except ImportError:  # pragma: no cover - allow import without binary installed
    Llama = None


DEFAULTS = {
    "model_path": str(Path.home() / ".claude-env/models/nomic-embed-text-v1.5.Q8_0.gguf"),
    "n_ctx": 2048,
    "n_threads": os.cpu_count() or 8,
    "n_gpu_layers": -1,           # offload all to Metal on Apple Silicon
    "embedding_dim": 768,
}


class LlamaEmbedder:
    def __init__(self, model_path: str | None = None, n_ctx: int | None = None,
                 n_gpu_layers: int | None = None):
        self.dim = DEFAULTS["embedding_dim"]
        self._model_path = model_path or DEFAULTS["model_path"]
        if Llama is None:
            raise RuntimeError(
                "llama-cpp-python not installed. "
                "Install: CMAKE_ARGS='-DLLAMA_METAL=on' pip install llama-cpp-python")
        self.llm = Llama(
            model_path=self._model_path,
            embedding=True,
            n_ctx=n_ctx or DEFAULTS["n_ctx"],
            n_threads=DEFAULTS["n_threads"],
            n_gpu_layers=n_gpu_layers if n_gpu_layers is not None
            else DEFAULTS["n_gpu_layers"],
            verbose=False,
        )

    @staticmethod
    def _doc(t: str) -> str:
        return f"search_document: {t}"

    @staticmethod
    def _query(t: str) -> str:
        return f"search_query: {t}"

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
