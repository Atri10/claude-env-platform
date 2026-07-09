"""Coverage for the incremental RAG re-index the git post-commit hook triggers.
Run: pytest tests/ -q

The post-commit hook (scripts/post-commit) runs rag/incremental_index.py, which
calls Indexer.incremental(changed_files). This is the mechanism that keeps a
repo's RAG index current on every commit. These tests exercise the real
Indexer.incremental() logic against a temp git repo and a temp DB, with the
model-dependent pieces (embedder, LanceDB store) replaced by lightweight fakes
so no model download or lancedb install is needed.

Behaviors covered:
  * an added/modified allowed file is indexed (chunks upserted + rag_file_state row)
  * a deleted file is removed from the store and rag_file_state
  * a policy-blocked file (e.g. a .env) is skipped, never indexed
  * unchanged content (same hash) on a second run is skipped — no re-embed
  * incremental() works from an empty base (no prior full index required)
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


class _FakeEmbedder:
    """Deterministic, model-free embedder. dim is small; vectors are constant."""
    dim = 8
    model_name = "fake-embedder"        # read by _record_index_state

    def embed_documents(self, texts):
        return [[0.0] * self.dim for _ in texts]


class _FakeStore:
    """Records upsert/delete calls so tests can assert what the indexer did,
    without a real LanceDB backend. Tracks a running row count per (repo,branch)
    so count() (used by _record_index_state) returns something sensible."""
    def __init__(self):
        self.upserts = []   # list of (repo, branch, n_rows)
        self.deletes = []   # list of (repo, branch, file_path)
        self._rows = {}     # (repo, branch) -> int

    def upsert(self, repo, branch, rows):
        self.upserts.append((repo, branch, len(rows)))
        self._rows[(repo, branch)] = self._rows.get((repo, branch), 0) + len(rows)
        return len(rows)

    def delete_file(self, repo, branch, file_path):
        self.deletes.append((repo, branch, file_path))

    def count(self, repo, branch):
        return self._rows.get((repo, branch), 0)


def _init_repo(tmp_path: Path, tier: int = 0) -> Path:
    """Create a real git repo with a claude-env repo-policy.yaml (so the Indexer
    resolves a slug, tier, and RAG scope), commit an initial source file."""
    repo = tmp_path / "demo-repo"
    (repo / ".claude").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / ".claude" / "repo-policy.yaml").write_text(
        "repo: demo-repo\n"
        f"tier: {tier}\n"
        "deny:\n"
        "  extensions: ['.env']\n"
        "rag:\n"
        "  enabled: true\n"
        "  index_paths: ['**']\n"
        "  exclude_paths: []\n"
        "  index_only_committed: true\n")
    (repo / "src" / "app.py").write_text("def hello():\n    return 'hi'\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def _indexer(repo: Path):
    """Build a real Indexer, then inject the fakes so incremental() runs without
    a model or lancedb. Bypasses _lazy() by pre-setting embedder/store."""
    from rag.indexers.indexer import Indexer
    idx = Indexer(str(repo))
    idx.embedder = _FakeEmbedder()
    idx.store = _FakeStore()
    # _lazy() is a no-op once embedder is set, so injection sticks.
    return idx


def test_incremental_indexes_a_changed_allowed_file(tmp_path):
    _fresh_db()
    repo = _init_repo(tmp_path)
    idx = _indexer(repo)

    result = idx.incremental(["src/app.py"])

    assert result["repo"] == "demo-repo"
    assert result["files"] == 1
    assert result["chunks"] >= 1
    assert idx.store.upserts, "expected chunks upserted to the store"
    # rag_file_state row recorded so a later run can detect no-change
    row = idx.db.query_one(
        "SELECT chunk_count FROM rag_file_state WHERE repo=? AND file_path=?",
        ("demo-repo", "src/app.py"))
    assert row is not None and row["chunk_count"] >= 1


def test_incremental_from_empty_base_needs_no_prior_full_index(tmp_path):
    """The onboarding-crash case: a repo whose full index never ran. Incremental
    must still index changed files from an empty rag_file_state."""
    db = _fresh_db()
    repo = _init_repo(tmp_path)
    # confirm truly empty base
    assert db.query_one("SELECT COUNT(*) n FROM rag_file_state")["n"] == 0
    idx = _indexer(repo)
    result = idx.incremental(["src/app.py"])
    assert result["files"] == 1 and result["chunks"] >= 1


def test_incremental_skips_unchanged_content_on_second_run(tmp_path):
    _fresh_db()
    repo = _init_repo(tmp_path)

    idx1 = _indexer(repo)
    idx1.incremental(["src/app.py"])           # first pass: indexed

    idx2 = _indexer(repo)                       # fresh store spy, same DB
    result2 = idx2.incremental(["src/app.py"])  # content identical -> hash match

    assert result2["files"] == 0, "unchanged file should not be re-indexed"
    assert not idx2.store.upserts, "no re-embed/upsert for unchanged content"


def test_incremental_removes_a_deleted_file(tmp_path):
    _fresh_db()
    repo = _init_repo(tmp_path)

    idx1 = _indexer(repo)
    idx1.incremental(["src/app.py"])           # index it first
    assert idx1.db.query_one(
        "SELECT 1 FROM rag_file_state WHERE repo=? AND file_path=?",
        ("demo-repo", "src/app.py")) is not None

    # delete the file on disk (as a commit that removes it would leave it)
    (repo / "src" / "app.py").unlink()
    idx2 = _indexer(repo)
    idx2.incremental(["src/app.py"])           # now missing on disk

    # store.delete_file was called for this path (branch is whatever git uses)
    assert any(repo_ == "demo-repo" and fp == "src/app.py"
               for (repo_, _branch, fp) in idx2.store.deletes), idx2.store.deletes
    assert idx2.db.query_one(
        "SELECT 1 FROM rag_file_state WHERE repo=? AND file_path=?",
        ("demo-repo", "src/app.py")) is None, "state row should be deleted"


def test_incremental_skips_policy_blocked_file(tmp_path):
    _fresh_db()
    repo = _init_repo(tmp_path)
    # a secret file that the policy (.env deny) must block from indexing
    (repo / ".env").write_text("API_KEY=super-secret-value\n")

    idx = _indexer(repo)
    result = idx.incremental([".env"])

    assert result["files"] == 0, ".env must never be indexed"
    assert not idx.store.upserts
    assert idx.db.query_one(
        "SELECT 1 FROM rag_file_state WHERE repo=? AND file_path=?",
        ("demo-repo", ".env")) is None


def test_incremental_disabled_when_rag_off(tmp_path):
    _fresh_db()
    repo = _init_repo(tmp_path, tier=0)
    # flip rag off in the policy and rebuild the indexer
    pol = repo / ".claude" / "repo-policy.yaml"
    pol.write_text(pol.read_text().replace("enabled: true", "enabled: false"))

    idx = _indexer(repo)
    result = idx.incremental(["src/app.py"])
    assert result == {"skipped": True}
