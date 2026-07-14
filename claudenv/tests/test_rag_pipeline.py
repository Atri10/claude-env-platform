"""
Tests for the RAG indexing/retrieval pipeline (domain chunking, RagIndexer,
RagService, and the LanceDB retriever adapter's query-embedding + reranking
step) — covering bugs found in the hexagonal-architecture refactor:

  * sliding_window_chunks() used its `target_chars`/`overlap_chars` config as
    line counts instead of character counts, so windows were never actually
    sized by character length (a 50-line file produced a single giant chunk
    regardless of the configured target).
  * RagIndexer re-implemented chunking inline instead of delegating to the
    domain ChunkerFactory (rag_chunker.py), silently dropping tree-sitter
    code chunking and header-aware markdown chunking.
  * RagIndexer.index_file/index_batch tried to set `chunk.embedding = vector`
    on Chunk, a frozen+slotted dataclass with no such field -- guaranteed
    crash on every index call.
  * RagIndexer.full_index tried to mutate `state.chunk_count` on IndexState,
    also frozen+slotted -- guaranteed crash.
  * LanceDbRagRetriever.search() tried to mutate `query.query_vector` on
    RetrievalQuery, also frozen+slotted -- guaranteed crash; and the DI
    container wired the raw LanceDbVectorStore in as the retriever instead
    of LanceDbRagRetriever, silently skipping reranking entirely.
  * Once RagIndexer actually delegates to ChunkerFactory, real chunks carry
    a non-empty Chunk.metadata (chunker name, markdown header, ...); LanceDB's
    fixed pyarrow schema has no column for that, so `tbl.add()` raised
    "Invalid input, field '...' does not exist in table schema" for every
    chunk with metadata. Fixed by folding unknown fields into one
    `metadata` JSON column on write and expanding it back out on read.

No real LanceDB or embedding model is used here -- IRagRetriever-shaped upsert
store and IEmbeddingProvider are faked, matching the pattern in
test_memory_recall.py / test_audit_reporting.py. The metadata-folding fix is
covered directly against LanceDbVectorStore's pure helper methods (no DB
connection needed for those).
"""
from __future__ import annotations

import json

from claudenv.application.rag import RagIndexer, RagService
from claudenv.domain.rag import RAGConfig, RetrievalMode, RetrievalQuery, RetrievalResult
from claudenv.domain.rag_chunker import (
    ChunkerFactory,
    ChunkingConfig,
    MarkdownChunker,
    WindowConfig,
    make_chunker_factory,
    sliding_window_chunks,
)
from claudenv.domain.value_objects import BranchName, RepoSlug, Tier

REPO = RepoSlug.from_string("acme-widgets")
BRANCH = BranchName.from_string("main")


# ============================================================================
# Fakes
# ============================================================================

class FakeEmbeddingProvider:
    """IEmbeddingProvider fake: deterministic fixed-width zero vectors."""

    model_name = "fake-embedder"

    def __init__(self, dim: int = 4):
        self.dim = dim
        self.embed_document_calls: list[list[str]] = []
        self.embed_query_calls: list[str] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.embed_document_calls.append(list(texts))
        return [[0.1] * self.dim for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        self.embed_query_calls.append(text)
        return [0.2] * self.dim


class FakeStore:
    """IRagRetriever-shaped fake used as RagIndexer's upsert target."""

    def __init__(self):
        self.upserted: list[tuple] = []
        self.deleted_files: list[tuple] = []

    def upsert(self, repo, branch, rows: list[dict]) -> int:
        self.upserted.append((repo, branch, rows))
        return len(rows)

    def delete_file(self, repo, branch, file_path) -> None:
        self.deleted_files.append((repo, branch, file_path))

    def search(self, query):
        return []

    def count(self, repo, branch) -> int:
        return sum(len(rows) for _, _, rows in self.upserted)


class FakeBookkeeping:
    """IRagBookkeeping fake."""

    def __init__(self):
        self.file_hashes: dict[tuple, tuple] = {}
        self.index_states: dict[tuple, object] = {}

    def get_file_hash(self, repo, branch, file_path):
        entry = self.file_hashes.get((str(repo), str(branch), file_path))
        return entry[0] if entry else None

    def set_file_hash(self, repo, branch, file_path, content_hash, chunk_count) -> None:
        self.file_hashes[(str(repo), str(branch), file_path)] = (content_hash, chunk_count)

    def delete_file_hash(self, repo, branch, file_path) -> None:
        self.file_hashes.pop((str(repo), str(branch), file_path), None)

    def get_index_state(self, repo, branch):
        return self.index_states.get((str(repo), str(branch)))

    def set_index_state(self, state) -> None:
        self.index_states[(str(state.repo), str(state.branch))] = state


class FakeRetriever:
    """IRagRetriever fake standing in for LanceDbRagRetriever, so RagService
    tests don't need a real vector store."""

    def __init__(self, results: list[RetrievalResult] | None = None):
        self.results = results or []
        self.last_query: RetrievalQuery | None = None

    def search(self, query: RetrievalQuery) -> list[RetrievalResult]:
        self.last_query = query
        return self.results

    def count(self, repo, branch) -> int:
        return len(self.results)


class FakeReranker:
    def __init__(self):
        self.calls: list[tuple] = []

    def rerank(self, query: str, docs: list[str], top_k: int) -> list[int]:
        self.calls.append((query, tuple(docs), top_k))
        # reverse the order, distinguishable from identity passthrough
        return list(reversed(range(len(docs))))[:top_k]


def make_indexer(embedder=None) -> tuple[RagIndexer, FakeStore, FakeBookkeeping]:
    store = FakeStore()
    bookkeeping = FakeBookkeeping()
    embedder = embedder or FakeEmbeddingProvider()
    cfg = RAGConfig(embedding_dim=4, chunk_target_tokens=64, chunk_overlap_tokens=8)
    indexer = RagIndexer(
        repo=REPO, branch=BRANCH, store=store, bookkeeping=bookkeeping,
        embedder=embedder, chunker=cfg,
    )
    return indexer, store, bookkeeping


# ============================================================================
# Domain chunking: sliding_window_chunks char-size regression
# ============================================================================

class TestSlidingWindowChunks:
    def test_target_chars_is_a_character_budget_not_a_line_count(self):
        # 50 short lines is well under 50 * anything, but each line is short
        # (~7 chars incl. newline) -- a correct char-budget of 100 chars
        # should produce several chunks, not one giant chunk covering all 50
        # lines (the pre-fix bug: target_chars=100 was used as "100 lines").
        text = "\n".join(f"line {i}" for i in range(50))
        chunks = sliding_window_chunks(text, WindowConfig(
            target_chars=100, overlap_chars=20, min_chars=1,
        ))
        assert len(chunks) > 1
        for start, end, chunk_text in chunks:
            assert len(chunk_text) <= 100 + 20  # generous slack for the boundary line
            assert start <= end

    def test_single_short_text_yields_one_chunk(self):
        text = "line 1\nline 2\nline 3"
        chunks = sliding_window_chunks(text, WindowConfig(target_chars=2000, overlap_chars=200))
        assert len(chunks) == 1
        assert chunks[0] == (1, 3, text)

    def test_empty_text_yields_no_chunks(self):
        assert sliding_window_chunks("", WindowConfig()) == []

    def test_overlap_always_makes_forward_progress(self):
        # overlap_chars >= target_chars used to be able to keep the *entire*
        # buffer as "overlap", never advancing `start` and looping forever.
        text = "\n".join(f"line {i}" for i in range(200))
        chunks = sliding_window_chunks(text, WindowConfig(
            target_chars=50, overlap_chars=10_000, min_chars=1,
        ))
        assert len(chunks) > 1
        starts = [c[0] for c in chunks]
        assert starts == sorted(starts)
        assert starts[-1] <= 200


# ============================================================================
# Domain chunking: ChunkerFactory dispatch across file types
# ============================================================================

class TestChunkerFactory:
    def test_python_file_uses_code_chunker_and_produces_non_empty_chunks(self):
        factory = make_chunker_factory()
        text = (
            "def foo():\n"
            "    return 1\n"
            "\n\n"
            "class Bar:\n"
            "    def baz(self):\n"
            "        return 2\n"
        )
        chunks = factory.chunk("pkg/mod.py", text, REPO, BRANCH, "deadbeef")
        assert len(chunks) > 0
        assert all(c.text.strip() for c in chunks)
        assert all(c.file_path == "pkg/mod.py" for c in chunks)

    def test_markdown_file_uses_header_aware_chunker(self):
        factory = make_chunker_factory()
        text = "# Title\nintro text\n\n## Section A\nbody a\n\n## Section B\nbody b\n"
        chunks = factory.chunk("docs/readme.md", text, REPO, BRANCH, "deadbeef")
        assert len(chunks) > 0
        assert all(c.file_type == "markdown" for c in chunks)
        headers = {c.metadata.get("header") for c in chunks}
        assert "Title" in headers or "Section A" in headers

    def test_plain_text_file_uses_fallback_sliding_window(self):
        factory = make_chunker_factory()
        text = "\n".join(f"some plain text line number {i}" for i in range(100))
        chunks = factory.chunk("notes.txt", text, REPO, BRANCH, "deadbeef")
        assert len(chunks) > 0
        assert all(c.metadata.get("chunker") == "fallback" for c in chunks)

    def test_get_chunker_routes_by_extension(self):
        factory = ChunkerFactory()
        assert isinstance(factory.get_chunker("a.md"), MarkdownChunker)
        assert factory.get_chunker("a.py") is factory._code
        assert factory.get_chunker("a.unknownext") is factory._fallback


# ============================================================================
# RagIndexer: delegates to ChunkerFactory, doesn't crash on frozen dataclasses
# ============================================================================

class TestRagIndexerRoundTrip:
    def test_index_file_python_produces_rows_with_vectors(self):
        indexer, store, bookkeeping = make_indexer()
        text = "def handler():\n    return 42\n"

        n = indexer.index_file(REPO, BRANCH, "c0", "app/handler.py", text)

        assert n > 0
        assert len(store.upserted) == 1
        _, _, rows = store.upserted[0]
        assert len(rows) == n
        for row in rows:
            assert "vector" in row and len(row["vector"]) == 4
            assert row["file_path"] == "app/handler.py"
        # bookkeeping recorded the file hash so re-indexing unchanged content is a no-op
        assert bookkeeping.get_file_hash(REPO, BRANCH, "app/handler.py") is not None

    def test_index_file_skips_unchanged_content(self):
        indexer, store, _ = make_indexer()
        text = "x = 1\n"
        indexer.index_file(REPO, BRANCH, "c0", "a.py", text)
        assert len(store.upserted) == 1

        n = indexer.index_file(REPO, BRANCH, "c1", "a.py", text)
        assert n == 0
        assert len(store.upserted) == 1  # no second upsert

    def test_index_file_markdown_uses_header_chunking(self):
        indexer, store, _ = make_indexer()
        text = "# Intro\nhello\n\n## Details\nmore text\n"
        n = indexer.index_file(REPO, BRANCH, "c0", "README.md", text)
        assert n > 0
        _, _, rows = store.upserted[0]
        assert any(r["file_type"] == "markdown" for r in rows)

    def test_full_index_reports_correct_totals_without_crashing(self):
        indexer, store, bookkeeping = make_indexer()
        files = {
            "a.py": "def f():\n    return 1\n",
            "b.md": "# Title\nsome content here\n",
            "c.txt": "plain text content\nmore lines\n",
        }

        result = indexer.full_index(REPO, BRANCH, files)

        assert result["files"] == 3
        assert result["chunks"] > 0
        state = bookkeeping.get_index_state(REPO, BRANCH)
        assert state is not None
        assert state.chunk_count == result["chunks"]

    def test_index_batch_attaches_vectors_without_mutating_chunk(self):
        indexer, store, bookkeeping = make_indexer()
        factory = make_chunker_factory()
        chunks = factory.chunk("x.py", "def f():\n    pass\n", REPO, BRANCH, "c0")

        n = indexer.index_batch(REPO, BRANCH, "c0", chunks)

        assert n == len(chunks)
        _, _, rows = store.upserted[0]
        assert all("vector" in r for r in rows)
        assert bookkeeping.get_file_hash(REPO, BRANCH, "x.py") is not None

    def test_accepts_prebuilt_chunker_factory_directly(self):
        store = FakeStore()
        bookkeeping = FakeBookkeeping()
        embedder = FakeEmbeddingProvider()
        factory = make_chunker_factory(ChunkingConfig(chunk_target_tokens=32, chunk_overlap_tokens=4))
        indexer = RagIndexer(
            repo=REPO, branch=BRANCH, store=store, bookkeeping=bookkeeping,
            embedder=embedder, chunker=factory,
        )
        n = indexer.index_file(REPO, BRANCH, "c0", "a.py", "def f():\n    return 1\n")
        assert n > 0


# ============================================================================
# RagService.search + reranking wiring
# ============================================================================

class TestRagServiceSearch:
    def test_search_builds_retrieval_query_and_delegates_to_retriever(self):
        embedder = FakeEmbeddingProvider()
        retriever = FakeRetriever(results=[])
        reranker = FakeReranker()
        indexer, _, _ = make_indexer(embedder)
        service = RagService(indexer=indexer, retriever=retriever, embedder=embedder, reranker=reranker)

        service.search(REPO, BRANCH, "how does auth work", top_k=5)

        assert retriever.last_query is not None
        assert retriever.last_query.query == "how does auth work"
        assert retriever.last_query.query_vector == [0.2] * 4
        assert retriever.last_query.top_k == 5
        assert retriever.last_query.repo_filter == REPO
        assert retriever.last_query.branch_filter == BRANCH


class TestLanceDbRagRetrieverWiring:
    """These exercise the fixed frozen-dataclass mutation bug and the
    reranking step, using a fake vector store instead of real LanceDB."""

    def test_search_embeds_query_without_mutating_frozen_query(self):
        from claudenv.adapters.vector.lancedb import LanceDbRagRetriever

        chunk_result = _fake_result("hello world")
        store = FakeStore()
        store.search = lambda q: [chunk_result]  # type: ignore[method-assign]
        embedder = FakeEmbeddingProvider()
        retriever = LanceDbRagRetriever(store=store, embedder=embedder, reranker=None)

        query = RetrievalQuery(
            query="hello", query_vector=None, top_k=5, mode=RetrievalMode.HYBRID,
            repo_filter=REPO, branch_filter=BRANCH, tier_filter=None,
        )

        results = retriever.search(query)

        assert results == [chunk_result]
        # original query object must remain unmutated (it's frozen)
        assert query.query_vector is None
        assert embedder.embed_query_calls == ["hello"]

    def test_search_applies_reranker_when_present(self):
        from claudenv.adapters.vector.lancedb import LanceDbRagRetriever

        r1, r2 = _fake_result("doc one"), _fake_result("doc two")
        store = FakeStore()
        store.search = lambda q: [r1, r2]  # type: ignore[method-assign]
        embedder = FakeEmbeddingProvider()
        reranker = FakeReranker()
        retriever = LanceDbRagRetriever(store=store, embedder=embedder, reranker=reranker)

        query = RetrievalQuery(
            query="hello", query_vector=[0.1] * 4, top_k=2, mode=RetrievalMode.HYBRID,
            repo_filter=REPO, branch_filter=BRANCH, tier_filter=None,
        )

        results = retriever.search(query)

        assert len(reranker.calls) == 1
        # FakeReranker reverses order -- assert the rerank actually took effect
        assert results == [r2, r1]


def _fake_result(text: str) -> RetrievalResult:
    from claudenv.domain.rag import Chunk, ChunkType
    from claudenv.domain.value_objects import ChunkId, ContentHash

    chunk = Chunk(
        chunk_id=ChunkId.from_parts(REPO, "f.py", 1, 2),
        repo=REPO, branch=BRANCH, commit_sha="c0", file_path="f.py", file_type="py",
        symbol_type=ChunkType.WINDOW, symbol_name="chunk_0", start_line=1, end_line=2,
        text=text, content_hash=ContentHash.compute(text), tier=Tier.INTERNAL,
    )
    return RetrievalResult(chunk=chunk, score=1.0, rank=1)


# ============================================================================
# LanceDbVectorStore metadata <-> schema folding (pure helpers, no real DB)
# ============================================================================

class TestLanceDbMetadataFolding:
    """LanceDB's pyarrow schema is fixed; Chunk.to_metadata() can include
    arbitrary extra keys via Chunk.metadata. _to_row()/_expand_metadata()
    must round-trip those extra keys through one `metadata` JSON column
    instead of crashing `tbl.add()` on an unknown field."""

    def _store(self):
        from claudenv.adapters.vector.lancedb import LanceDbVectorStore
        # Bypass __init__ (which opens a real lancedb connection) since these
        # helpers are pure functions of self._CORE_FIELDS.
        return object.__new__(LanceDbVectorStore)

    def test_to_row_folds_unknown_fields_into_metadata_json(self):
        store = self._store()
        row = {
            "chunk_id": "abc", "vector": [0.1, 0.2], "text": "hi",
            "repo": "r", "branch": "main", "commit_sha": "c0",
            "file_path": "f.py", "file_type": "py", "symbol_type": "window",
            "symbol_name": "chunk_0", "start_line": 1, "end_line": 2,
            "content_hash": "h", "tier": 1,
            "chunker": "tree_sitter", "node_type": "function_definition",
        }
        out = store._to_row(row)
        assert "chunker" not in out
        assert "node_type" not in out
        assert out["chunk_id"] == "abc"
        assert json.loads(out["metadata"]) == {
            "chunker": "tree_sitter", "node_type": "function_definition",
        }

    def test_to_row_with_no_extra_metadata_produces_empty_json_object(self):
        store = self._store()
        row = {"chunk_id": "abc", "text": "hi"}
        out = store._to_row(row)
        assert json.loads(out["metadata"]) == {}

    def test_expand_metadata_round_trips_through_to_row(self):
        store = self._store()
        original = {
            "chunk_id": "abc", "text": "hi", "chunker": "markdown", "header": "Intro",
        }
        written = store._to_row(dict(original))
        # simulate reading the row back from LanceDB
        read_back = dict(written)
        expanded = store._expand_metadata(read_back)
        assert expanded["chunker"] == "markdown"
        assert expanded["header"] == "Intro"
        assert "metadata" not in expanded

    def test_expand_metadata_tolerates_missing_or_malformed_metadata(self):
        store = self._store()
        assert store._expand_metadata({"chunk_id": "abc"}) == {"chunk_id": "abc"}
        assert store._expand_metadata({"chunk_id": "abc", "metadata": "not json"}) == {"chunk_id": "abc"}
