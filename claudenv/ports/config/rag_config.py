"""
claude-env :: Ports - RAG configuration value object
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RagConfig:
    embedding_dim: int = 768
    embedding_backend: str = "llama_cpp"
    embedding_model_path: str = ""
    embedding_model_name: str = ""
    embedding_n_ctx: int = 2048
    embedding_n_gpu_layers: int = -1
    embedding_document_prefix: str = ""
    embedding_query_prefix: str = ""
    embedding_pooling_type: str = "mean"
    reranker_backend: str = "onnx_cross_encoder"
    reranker_model_dir: str = ""
