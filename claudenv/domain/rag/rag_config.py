"""
claude-env :: Domain - RAG Entities - RAGConfig
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RAGConfig:
    """RAG pipeline configuration."""
    embedding_dim: int
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
    chunk_target_tokens: int = 512
    chunk_overlap_tokens: int = 64
    hybrid_alpha: float = 0.5  # RRF fusion weight
    max_context_tokens: int = 4000
