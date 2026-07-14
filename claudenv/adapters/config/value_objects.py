"""
claude-env :: Adapters - Configuration - Value Objects

EmbeddingConfig and RerankerConfig, merged from the previously separate
embedding_config.py / reranker_config.py modules.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EmbeddingConfig:
    backend: str = "llama_cpp"
    model_path: str = ""
    model_name: str = ""
    n_ctx: int = 2048
    n_gpu_layers: int = -1
    embedding_dim: int = 768
    document_prefix: str = ""
    query_prefix: str = ""
    pooling_type: str = "mean"


@dataclass
class RerankerConfig:
    backend: str = "onnx_cross_encoder"
    model_dir: str = ""
