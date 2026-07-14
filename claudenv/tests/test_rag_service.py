"""
Tests for claudenv application RAG service and indexer:

  - RagIndexer.index_file()/index_batch() used to do `chunk.embedding = vector`,
    but Chunk is a frozen(+slots) dataclass with no `embedding` field at all,
    so every index call raised (a TypeError under slots, FrozenInstanceError
    otherwise). The embedding has to travel through Chunk.metadata (the only
    mutable extension point to_metadata()/from_metadata() round-trip), via
    dataclasses.replace() since the object is frozen.
  - RagService.search() had a `reranker` field that nothing ever called --
    configuring a reranker model had no effect on result ordering.
  - RagService.index_repo()/get_index_state()/get_chunk() didn't exist at all;
    the lancedb_rag MCP server called them anyway (see
    claudenv/adapters/mcp/lancedb_rag/server.py's _do_index/_do_status/
    _do_get_chunk), which is why they're covered here as real methods.
"""
from __future__ import annotations

from claudenv.application.rag import RagIndexer, RagService
from claudenv.domain.rag import RetrievalResult
from claudenv.domain.value_objects import BranchName, ContentHash, RepoSlug, Tier


class FakeEmbedder:
    def embed_documents(self, texts):
        return [[1.0, 0.0, 0.0] for _ in texts]

    def embed_query(self, text):
        return [1.0, 0.0, 0.0]

    model_name = "fake"


class FakeBookkeeping:
    def __init__(self):
        self.hashes = {}
        self.states = {}

    def get_file_hash(self, repo, branch, file_path):
        return self.hashes.get((str(repo), str(branch), file_path))

    def set_file_hash(self, repo, branch, file_path, content_hash, chunk_count):
        self.hashes[(str(repo), str(branch), file_path)] = content_hash

    def delete_file_hash(self, repo, branch, file_path):
        self.hashes.pop((str(repo), str(branch), file_path), None)

    def get_index_state(self, repo, branch):
        return self.states.get((str(repo), str(branch)))

    def set_index_state(self, state):
        self.states[(str(state.repo), str(state.branch))] = state


class FakeStore:
    def __init__(self):
        self.upserted_rows: list[dict] = []

    def upsert(self, repo, branch, rows):
        self.upserted_rows.extend(rows)
        return len(rows)

    def delete_file(self, repo, branch, file_path):
        pass

    def search(self, query):
        return [
            RetrievalResult(chunk=_fake_chunk("a"), score=0.5, rank=1),
            RetrievalResult(chunk=_fake_chunk("b"), score=0.9, rank=2),
        ]

    def count(self, repo, branch):
        return len(self.upserted_rows)


def _fake_chunk(name):
    from claudenv.domain.rag import Chunk, ChunkId, ChunkType
    return Chunk(
        chunk_id=ChunkId.from_string(f"c-{name}"),
        repo=RepoSlug.from_string("r"), branch=BranchName.from_string("main"),
        commit_sha="0", file_path=f"{name}.py", file_type="py",
        symbol_type=ChunkType.WINDOW, symbol_name=name,
        start_line=1, end_line=2, text=f"text {name}",
        content_hash=ContentHash.compute(name), tier=Tier.INTERNAL,
    )


class FakeReranker:
    def __init__(self, order):
        self.order = order
        self.calls = []

    def rerank(self, query, docs, top_k):
        self.calls.append((query, docs, top_k))
        return self.order


class TestRagIndexerEmbedding:
    def test_index_file_attaches_embedding_via_replace_not_attribute_assignment(self):
        store = FakeStore()
        bookkeeping = FakeBookkeeping()
        indexer = RagIndexer(
            repo=RepoSlug.from_string("r"), branch=BranchName.from_string("main"),
            store=store, bookkeeping=bookkeeping, embedder=FakeEmbedder(),
            chunker=_fake_config(),
        )

        n = indexer.index_file(
            RepoSlug.from_string("r"), BranchName.from_string("main"), "abc123",
            "src/app.py", "def f():\n    return 1\n",
        )

        assert n > 0
        assert store.upserted_rows
        # The embedding must have made it into the row LanceDB will persist.
        assert "vector" in store.upserted_rows[0]
        assert store.upserted_rows[0]["vector"] == [1.0, 0.0, 0.0]

    def test_index_file_skips_unchanged_content(self):
        store = FakeStore()
        bookkeeping = FakeBookkeeping()
        indexer = RagIndexer(
            repo=RepoSlug.from_string("r"), branch=BranchName.from_string("main"),
            store=store, bookkeeping=bookkeeping, embedder=FakeEmbedder(),
            chunker=_fake_config(),
        )
        args = (RepoSlug.from_string("r"), BranchName.from_string("main"), "abc123",
                "src/app.py", "def f():\n    return 1\n")

        first = indexer.index_file(*args)
        second = indexer.index_file(*args)

        assert first > 0
        assert second == 0  # unchanged -> skipped

    def test_full_index_sets_chunk_count_without_mutating_frozen_state(self):
        store = FakeStore()
        bookkeeping = FakeBookkeeping()
        indexer = RagIndexer(
            repo=RepoSlug.from_string("r"), branch=BranchName.from_string("main"),
            store=store, bookkeeping=bookkeeping, embedder=FakeEmbedder(),
            chunker=_fake_config(),
        )

        result = indexer.full_index(
            RepoSlug.from_string("r"), BranchName.from_string("main"),
            {"a.py": "def a(): pass", "b.py": "def b(): pass"},
        )

        assert result["files"] == 2
        state = bookkeeping.get_index_state(RepoSlug.from_string("r"), BranchName.from_string("main"))
        assert state.chunk_count == result["chunks"]


class TestRagServiceReranking:
    def test_search_applies_reranker_when_configured(self):
        store = FakeStore()
        reranker = FakeReranker(order=[1, 0])  # reverse the two fake results
        service = RagService(indexer=None, retriever=store, embedder=FakeEmbedder(), reranker=reranker)

        results = service.search(RepoSlug.from_string("r"), BranchName.from_string("main"), "query", top_k=2)

        assert reranker.calls  # reranker was actually invoked
        assert [r.chunk.symbol_name for r in results] == ["b", "a"]

    def test_search_without_reranker_returns_retriever_order(self):
        store = FakeStore()
        service = RagService(indexer=None, retriever=store, embedder=FakeEmbedder(), reranker=None)

        results = service.search(RepoSlug.from_string("r"), BranchName.from_string("main"), "query", top_k=2)

        assert [r.chunk.symbol_name for r in results] == ["a", "b"]


class TestRagServiceIndexRepo:
    def test_index_repo_walks_directory_and_indexes_text_files(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("def f():\n    return 1\n")
        (tmp_path / "binary.bin").write_bytes(b"\x00\x01\x02not text")
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "ignored").write_text("should not be indexed")

        store = FakeStore()
        bookkeeping = FakeBookkeeping()
        indexer = RagIndexer(
            repo=RepoSlug.from_string("r"), branch=BranchName.from_string("main"),
            store=store, bookkeeping=bookkeeping, embedder=FakeEmbedder(),
            chunker=_fake_config(),
        )
        service = RagService(indexer=indexer, retriever=store, embedder=FakeEmbedder(),
                              reranker=None, bookkeeping=bookkeeping)

        result = service.index_repo(RepoSlug.from_string("r"), BranchName.from_string("main"), tmp_path)

        assert result["files"] == 1  # binary and .git skipped
        indexed_paths = {row["file_path"] for row in store.upserted_rows}
        assert indexed_paths == {"src/app.py"}

    def test_get_index_state_returns_none_without_bookkeeping(self):
        service = RagService(indexer=None, retriever=FakeStore(), embedder=FakeEmbedder(), reranker=None)
        assert service.get_index_state(RepoSlug.from_string("r"), BranchName.from_string("main")) is None


def _fake_config():
    from claudenv.domain.rag import RAGConfig
    return RAGConfig(
        embedding_dim=3, embedding_backend="dummy", embedding_model_path="", embedding_model_name="",
        embedding_n_ctx=512, embedding_n_gpu_layers=0, embedding_document_prefix="",
        embedding_query_prefix="", embedding_pooling_type="mean",
        reranker_backend="", reranker_model_dir="",
    )
