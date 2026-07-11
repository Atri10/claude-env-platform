"""
claude-env :: embedder backend registry
File: rag/embeddings/registry.py

Maps the `embedding.backend` string in config/rag.yaml to a concrete
EmbedderBackend class. To add a new backend: implement EmbedderBackend
(rag/embeddings/base.py), add one line here. No factory or caller changes.
"""
from __future__ import annotations

from rag.embeddings.base import EmbedderBackend
from rag.embeddings.llama_embedder import LlamaEmbedder

BACKENDS: dict[str, type[EmbedderBackend]] = {
    "llama_cpp": LlamaEmbedder,
}


def get_backend_class(name: str) -> type[EmbedderBackend]:
    try:
        return BACKENDS[name]
    except KeyError:
        raise ValueError(
            f"Unknown embedding.backend '{name}'. Valid values: {sorted(BACKENDS)}.")
