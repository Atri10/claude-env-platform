"""
claude-env :: Adapters - Configuration - RAG
"""
from __future__ import annotations

from claudenv.domain.rag import RAGConfig
from claudenv.ports import IRAGConfig

from .base import _ConfigBase
from .models import EmbeddingConfig, RerankerConfig


class RAGConfigProvider(_ConfigBase, IRAGConfig):
    """RAG pipeline configuration provider."""

    def get_embedding_config(self) -> EmbeddingConfig:
        emb = self._raw_config.get("embedding", {})
        return EmbeddingConfig(
            backend=self._get_env("EMBED_BACKEND", emb.get("backend", "llama_cpp")),
            model_path=self._expand(self._get_env("EMBED_MODEL_PATH", emb.get("model_path", ""))),
            model_name=self._get_env("EMBED_MODEL_NAME", emb.get("model_name", "")),
            n_ctx=int(self._get_env("EMBED_CTX", str(emb.get("n_ctx", 2048)))),
            n_gpu_layers=int(self._get_env("EMBED_GPU_LAYERS", str(emb.get("n_gpu_layers", -1)))),
            embedding_dim=int(self._get_env("EMBED_DIM", str(emb.get("embedding_dim", 768)))),
            document_prefix=self._get_env("EMBED_DOC_PREFIX", emb.get("document_prefix", "")),
            query_prefix=self._get_env("EMBED_QUERY_PREFIX", emb.get("query_prefix", "")),
            pooling_type=self._get_env("EMBED_POOLING_TYPE", emb.get("pooling_type", "mean")),
        )

    def get_reranker_config(self) -> RerankerConfig:
        rer = self._raw_config.get("reranker", {})
        rer_dir = self._get_env("RERANKER_DIR", rer.get("model_dir", ""))
        return RerankerConfig(
            backend=self._get_env("RERANKER_BACKEND", rer.get("backend", "onnx_cross_encoder")),
            model_dir=self._expand(rer_dir) if rer_dir else "",
        )

    def get_rag_config(self) -> RAGConfig:
        emb = self.get_embedding_config()
        rer = self.get_reranker_config()
        return RAGConfig(
            embedding_dim=emb.embedding_dim,
            embedding_backend=emb.backend,
            embedding_model_path=emb.model_path,
            embedding_model_name=emb.model_name,
            embedding_n_ctx=emb.n_ctx,
            embedding_n_gpu_layers=emb.n_gpu_layers,
            embedding_document_prefix=emb.document_prefix,
            embedding_query_prefix=emb.query_prefix,
            embedding_pooling_type=emb.pooling_type,
            reranker_backend=rer.backend,
            reranker_model_dir=rer.model_dir,
        )
