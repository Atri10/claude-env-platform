"""
claude-env :: reranker backend registry
File: rag/rerankers/registry.py

Maps the `reranker.backend` string in config/rag.yaml to a concrete
RerankerBackend class. To add a new backend: implement RerankerBackend
(rag/rerankers/base.py), add one line here. No factory or caller changes.
"""
from __future__ import annotations

from rag.rerankers.base import RerankerBackend
from rag.rerankers.cross_encoder import CrossEncoderReranker

BACKENDS: dict[str, type[RerankerBackend]] = {
    "onnx_cross_encoder": CrossEncoderReranker,
}


def get_backend_class(name: str) -> type[RerankerBackend]:
    try:
        return BACKENDS[name]
    except KeyError:
        raise ValueError(
            f"Unknown reranker.backend '{name}'. Valid values: {sorted(BACKENDS)}.")
