"""Coverage for Indexer._index_one's non-finite (NaN/Inf) vector guard
(rag/indexers/indexer.py). Run: pytest tests/ -q

Regression context: an embedding model can occasionally emit NaN for a given
input (observed indexing a real repo's .gotmpl templating file with
Qwen3-VL-Embedding-8B). LlamaEmbedder._embed()'s L2-normalize step (`x / norm`)
propagates a single NaN into every component of that vector, and nothing caught
it before the row reached LanceDB, which rejects it with an opaque
"Vector column contains NaN values" Arrow error that names no file or chunk and
aborts indexing entirely (losing every already-embedded chunk in that upsert
batch). _index_one now filters non-finite vectors per-chunk, logs which file
was affected, and keeps indexing the rest of the file/repo.
"""
import importlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


def _fresh_db():
    dsn = "sqlite:///" + tempfile.mktemp(suffix=".db")
    os.environ["CLAUDE_ENV_DSN"] = dsn
    import lib.db as db
    importlib.reload(db)
    d = db.get_db()
    d.apply_schema(str(_ROOT / "sql" / "001_schema.sql"))
    return d


class _NanEmbedder:
    """Simulates a model that emits NaN for some inputs -- deterministic by
    input index so tests can assert exactly which chunk was skipped."""
    dim = 4
    model_name = "nan-embedder"

    def __init__(self, nan_at: set[int]):
        self._nan_at = nan_at

    def embed_documents(self, texts):
        out = []
        for i, _ in enumerate(texts):
            if i in self._nan_at:
                out.append([float("nan")] * self.dim)
            else:
                out.append([0.1, 0.2, 0.3, 0.4])
        return out


class _FakeStore:
    def __init__(self):
        self.upserts = []
        self.deletes = []
        self._rows = {}

    def upsert(self, repo, branch, rows):
        self.upserts.append((repo, branch, rows))
        self._rows[(repo, branch)] = self._rows.get((repo, branch), 0) + len(rows)
        return len(rows)

    def delete_file(self, repo, branch, file_path):
        self.deletes.append((repo, branch, file_path))

    def count(self, repo, branch):
        return self._rows.get((repo, branch), 0)


def _init_repo(tmp_path: Path, file_text: str) -> Path:
    repo = tmp_path / "demo-repo"
    (repo / ".claude").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / ".claude" / "repo-policy.yaml").write_text(
        "repo: demo-repo\n"
        "tier: 0\n"
        "rag:\n"
        "  enabled: true\n"
        "  index_paths: ['**']\n"
        "  exclude_paths: []\n"
        "  index_only_committed: true\n")
    (repo / "src" / "big.py").write_text(file_text)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def _indexer(repo: Path, embedder):
    from rag.indexers.indexer import Indexer
    idx = Indexer(str(repo))
    idx.embedder = embedder
    idx.store = _FakeStore()
    return idx


# A file big enough that chunk_file() splits it into multiple chunks, so we can
# target one chunk with a NaN vector while others stay valid.
_MULTI_CHUNK_FILE = "\n".join(f"def f_{i}():\n    return {i}\n" for i in range(400))


def test_chunk_with_nan_vector_is_skipped_not_the_whole_file(tmp_path):
    _fresh_db()
    repo = _init_repo(tmp_path, _MULTI_CHUNK_FILE)
    embedder = _NanEmbedder(nan_at={0})   # first chunk poisoned
    idx = _indexer(repo, embedder)

    result = idx.incremental(["src/big.py"])

    assert result["files"] == 1, "the file should still be indexed overall"
    upserted_rows = [r for (_repo, _branch, rows) in idx.store.upserts for r in rows]
    assert upserted_rows, "at least the good chunks must be upserted"
    assert all(
        all(v == v for v in row["vector"])   # v == v is False only for NaN
        for row in upserted_rows
    ), "no NaN vector must ever reach the store"


def test_all_chunks_nan_skips_the_file_entirely(tmp_path):
    _fresh_db()
    repo = _init_repo(tmp_path, _MULTI_CHUNK_FILE)
    embedder = _NanEmbedder(nan_at=set(range(1000)))  # poison everything
    idx = _indexer(repo, embedder)

    result = idx.incremental(["src/big.py"])

    assert result["files"] == 0
    assert not idx.store.upserts, "must not upsert an empty/all-NaN row set"


def test_inf_vector_is_also_skipped(tmp_path):
    _fresh_db()
    repo = _init_repo(tmp_path, "def f():\n    return 1\n")

    class _InfEmbedder:
        dim = 4
        model_name = "inf-embedder"
        def embed_documents(self, texts):
            return [[float("inf"), 0.1, 0.2, 0.3] for _ in texts]

    idx = _indexer(repo, _InfEmbedder())
    result = idx.incremental(["src/big.py"])
    assert result["files"] == 0
    assert not idx.store.upserts
