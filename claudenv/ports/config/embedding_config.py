"""
claude-env :: Ports - Embedding configuration value object
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
