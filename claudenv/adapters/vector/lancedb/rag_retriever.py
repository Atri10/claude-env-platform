"""
claude-env :: Adapters - LanceDB RAG Retriever
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from claudenv.domain.rag import (
    BranchName, Chunk, RetrievalQuery, RetrievalResult, RepoSlug,
)
from claudenv.ports import IRagRetriever

from .vector_store import LanceDbVectorStore


class LanceDbRagRetriever(IRagRetriever):
    """RAG retriever using LanceDB — adds query embedding + reranking on top
    of the raw vector store search."""

    def __init__(self, store: LanceDbVectorStore, embedder: Any, reranker: Any):
        self.store = store
        self.embedder = embedder
        self.reranker = reranker

    def search(self, query: RetrievalQuery) -> list[RetrievalResult]:
        # Embed query if vector not provided. RetrievalQuery is an immutable
        # (frozen, slotted) value object, so build a new instance rather than
        # mutating the one we were given.
        if query.query_vector is None and query.query:
            query = replace(query, query_vector=self.embedder.embed_query(query.query))

        results = self.store.search(query)

        # Rerank if available
        if self.reranker and query.query:
            texts = [r.chunk.text for r in results]
            reranked = self.reranker.rerank(query.query, texts, query.top_k)
            results = [results[i] for i in reranked]

        return results

    def count(self, repo: RepoSlug, branch: BranchName) -> int:
        return self.store.count(repo, branch)

    def get_chunk(self, repo: RepoSlug, branch: BranchName, chunk_id: str) -> Chunk | None:
        return self.store.get_chunk(repo, branch, chunk_id)
