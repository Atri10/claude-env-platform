"""Coverage for rag/git_sync.py -- the dispatch module behind the
post-commit/post-merge/post-checkout git hooks that keep a repo's RAG index
current for commits, pulls/merges, and branch switches.

Run: pytest tests/ -q

These tests exercise the real dispatch/lock logic against a temp git repo and
a temp DB, with the model-dependent Indexer pieces (embedder, LanceDB store)
replaced by lightweight fakes -- same approach as test_incremental_index.py --
so no model download or lancedb install is needed.
"""
import fcntl
import importlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import rag.git_sync as gs  # noqa: E402


def _fresh_db():
    dsn = "sqlite:///" + tempfile.mktemp(suffix=".db")
    os.environ["CLAUDE_ENV_DSN"] = dsn
    import lib.db as db
    importlib.reload(db)
    d = db.get_db()
    d.apply_schema(str(_ROOT / "sql" / "001_schema.sql"))
    return d


def test_lock_path_is_sanitized(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "HOME", tmp_path)
    p = gs._lock_path("my/repo", "feature branch")
    assert p == tmp_path / "state" / "locks" / "my-repo__feature_branch.lock"


def test_lock_acquired_when_free(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "HOME", tmp_path)
    with gs._repo_lock("demo", "main") as acquired:
        assert acquired is True


def test_lock_reports_contention(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "HOME", tmp_path)
    path = gs._lock_path("demo", "main")
    path.parent.mkdir(parents=True, exist_ok=True)
    holder = open(path, "w")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)   # simulate another run
    try:
        with gs._repo_lock("demo", "main") as acquired:
            assert acquired is False
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        holder.close()


def test_lock_released_after_use(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "HOME", tmp_path)
    with gs._repo_lock("demo", "main") as acquired:
        assert acquired is True
    # a second, later acquisition must succeed once the first is released
    with gs._repo_lock("demo", "main") as acquired2:
        assert acquired2 is True


def test_current_branch(tmp_path):
    repo = tmp_path / "r"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "f.txt").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    assert gs._current_branch(str(repo)) == "main"


def test_repo_slug_reads_policy_yaml(tmp_path):
    repo = tmp_path / "r"
    (repo / ".claude").mkdir(parents=True)
    (repo / ".claude" / "repo-policy.yaml").write_text("repo: my-slug\ntier: 0\n")
    assert gs._repo_slug(str(repo)) == "my-slug"


def test_repo_slug_falls_back_to_dirname_without_policy(tmp_path):
    repo = tmp_path / "some-dir"
    repo.mkdir()
    assert gs._repo_slug(str(repo)) == "some-dir"


class _FakeEmbedder:
    """Deterministic, model-free embedder -- same shape as
    test_incremental_index.py's fake, duplicated here per this test suite's
    existing convention of a self-contained fixture per file (no conftest.py
    exists in this repo)."""
    dim = 8
    model_name = "fake-embedder"

    def embed_documents(self, texts):
        return [[0.0] * self.dim for _ in texts]


class _FakeStore:
    def __init__(self):
        self.upserts = []
        self.deletes = []
        self._rows = {}

    def upsert(self, repo, branch, rows):
        self.upserts.append((repo, branch, len(rows)))
        self._rows[(repo, branch)] = self._rows.get((repo, branch), 0) + len(rows)
        return len(rows)

    def delete_file(self, repo, branch, file_path):
        self.deletes.append((repo, branch, file_path))

    def count(self, repo, branch):
        return self._rows.get((repo, branch), 0)


def _init_repo(tmp_path, tier: int = 0):
    repo = tmp_path / "demo-repo"
    (repo / ".claude").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / ".claude" / "repo-policy.yaml").write_text(
        "repo: demo-repo\n"
        f"tier: {tier}\n"
        "rag:\n"
        "  enabled: true\n"
        "  index_paths: ['**']\n"
        "  exclude_paths: []\n"
        "  index_only_committed: true\n")
    (repo / "src" / "app.py").write_text("def hello():\n    return 'hi'\n")
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def _fake_indexer(repo_root):
    from rag.indexers.indexer import Indexer
    idx = Indexer(str(repo_root))
    idx.embedder = _FakeEmbedder()
    idx.store = _FakeStore()
    return idx


def test_sync_commit_indexes_changed_files(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "HOME", tmp_path / "home")
    _fresh_db()
    repo = _init_repo(tmp_path)
    monkeypatch.setattr(gs, "_build_indexer", lambda root: _fake_indexer(root))

    result = gs.sync_commit(str(repo), ["src/app.py"])

    assert result["files"] == 1
    assert result["chunks"] >= 1


def test_sync_merge_indexes_changed_files(tmp_path, monkeypatch):
    """merge uses the identical strategy as commit -- given file list -> incremental()."""
    monkeypatch.setattr(gs, "HOME", tmp_path / "home")
    _fresh_db()
    repo = _init_repo(tmp_path)
    monkeypatch.setattr(gs, "_build_indexer", lambda root: _fake_indexer(root))

    result = gs.sync_merge(str(repo), ["src/app.py"])

    assert result["files"] == 1
    assert result["chunks"] >= 1


def test_sync_commit_skips_when_lock_held(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "HOME", tmp_path / "home")
    _fresh_db()
    repo = _init_repo(tmp_path)

    def _raising_indexer(root):
        raise AssertionError("_build_indexer must not run while the lock is held")

    # hold the lock externally, under the exact key sync_commit will compute
    # (_repo_slug reads .claude/repo-policy.yaml's repo: field directly, so
    # this doesn't need _build_indexer or a real Indexer to determine)
    lock_path = gs._lock_path("demo-repo", "main")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    holder = open(lock_path, "w")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        monkeypatch.setattr(gs, "_build_indexer", _raising_indexer)
        result = gs.sync_commit(str(repo), ["src/app.py"])
        assert result["skipped"] == "reindex already running"
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        holder.close()


def test_sync_checkout_ignores_file_level_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "HOME", tmp_path / "home")
    _fresh_db()
    repo = _init_repo(tmp_path)
    monkeypatch.setattr(gs, "_build_indexer", lambda root: _fake_indexer(root))

    result = gs.sync_checkout(str(repo), "deadbeef", "cafefeed", "0")

    assert result["skipped"] == "file-level checkout, not a branch switch"


def test_sync_checkout_full_indexes_a_never_indexed_branch(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "HOME", tmp_path / "home")
    _fresh_db()
    repo = _init_repo(tmp_path)
    monkeypatch.setattr(gs, "_build_indexer", lambda root: _fake_indexer(root))

    new_sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                             capture_output=True, text=True).stdout.strip()
    result = gs.sync_checkout(str(repo), "0" * 40, new_sha, "1")

    assert result["files"] == 1     # full_index() walked the one tracked file
    assert result["chunks"] >= 1


def test_sync_checkout_incrementally_catches_up_a_known_branch(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "HOME", tmp_path / "home")
    db = _fresh_db()
    repo = _init_repo(tmp_path)
    monkeypatch.setattr(gs, "_build_indexer", lambda root: _fake_indexer(root))

    first_sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                               capture_output=True, text=True).stdout.strip()
    gs.sync_commit(str(repo), ["src/app.py"])   # records rag_index_state row

    # a second commit lands (simulating a commit made elsewhere, then pulled)
    (repo / "src" / "app2.py").write_text("def bye():\n    return 'bye'\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "add app2"], check=True)
    second_sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                capture_output=True, text=True).stdout.strip()

    result = gs.sync_checkout(str(repo), first_sha, second_sha, "1")

    assert result["files"] == 1     # only the newly-committed file
    assert result["chunks"] >= 1


def test_sync_checkout_full_indexes_after_rebase_drops_recorded_commit(tmp_path, monkeypatch):
    """If the recorded last_commit was rewritten out of history (rebase/force-push
    elsewhere, then pulled), a plain `git diff last_commit..new_sha` fails and
    _git() silently discards the error, producing an empty changed-file list.
    sync_checkout must detect that last_commit is no longer an ancestor of
    new_sha and fall back to full_index() instead of a no-op incremental."""
    monkeypatch.setattr(gs, "HOME", tmp_path / "home")
    _fresh_db()
    repo = _init_repo(tmp_path)
    monkeypatch.setattr(gs, "_build_indexer", lambda root: _fake_indexer(root))

    gs.sync_commit(str(repo), ["src/app.py"])   # records rag_index_state row for the init commit
    first_sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                               capture_output=True, text=True).stdout.strip()

    # simulate a rebase/force-push: rewrite the recorded commit out of history,
    # changing the file's content so a real full_index would have work to do
    # (distinguishes "took the full_index path" from "took it but skipped
    # everything as unchanged")
    (repo / "src" / "app.py").write_text("def hello():\n    return 'hi again'\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--amend", "-m", "init amended"],
                   check=True)
    new_sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                             capture_output=True, text=True).stdout.strip()
    assert new_sha != first_sha

    result = gs.sync_checkout(str(repo), first_sha, new_sha, "1")

    assert result["files"] == 1     # full_index() walked the one tracked file,
                                     # not an empty incremental() no-op
    assert result["chunks"] >= 1


def test_main_dispatches_commit_event_from_argv(tmp_path, monkeypatch):
    """Exercises the hook-script -> main() argv seam for the commit event:
    main()'s args[0]/args[1]/args[2:] unpacking and routing to sync_commit,
    proven by an actual rag_file_state row (not just a non-crash)."""
    monkeypatch.setattr(gs, "HOME", tmp_path / "home")
    db = _fresh_db()
    repo = _init_repo(tmp_path)
    monkeypatch.setattr(gs, "_build_indexer", lambda root: _fake_indexer(root))
    monkeypatch.setattr(sys, "argv", ["git_sync.py", str(repo), "commit", "src/app.py"])

    assert gs.main() == 0

    row = db.query_one(
        "SELECT 1 FROM rag_file_state WHERE repo=? AND file_path=?",
        ("demo-repo", "src/app.py"))
    assert row is not None


def test_main_dispatches_merge_event_from_argv(tmp_path, monkeypatch):
    """Same seam, for the merge event (post-merge hook)."""
    monkeypatch.setattr(gs, "HOME", tmp_path / "home")
    db = _fresh_db()
    repo = _init_repo(tmp_path)
    monkeypatch.setattr(gs, "_build_indexer", lambda root: _fake_indexer(root))
    monkeypatch.setattr(sys, "argv", ["git_sync.py", str(repo), "merge", "src/app.py"])

    assert gs.main() == 0

    row = db.query_one(
        "SELECT 1 FROM rag_file_state WHERE repo=? AND file_path=?",
        ("demo-repo", "src/app.py"))
    assert row is not None


def test_main_dispatches_checkout_event_from_argv(tmp_path, monkeypatch):
    """Same seam, for the checkout event (post-checkout hook): verifies
    main()'s rest[0]/rest[1]/rest[2] unpacking into
    sync_checkout(repo_root, prev_sha, new_sha, is_branch_flag) is correct --
    a never-indexed branch here takes the full_index() path."""
    monkeypatch.setattr(gs, "HOME", tmp_path / "home")
    db = _fresh_db()
    repo = _init_repo(tmp_path)
    monkeypatch.setattr(gs, "_build_indexer", lambda root: _fake_indexer(root))
    new_sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                             capture_output=True, text=True).stdout.strip()
    monkeypatch.setattr(
        sys, "argv",
        ["git_sync.py", str(repo), "checkout", "0" * 40, new_sha, "1"])

    assert gs.main() == 0

    row = db.query_one(
        "SELECT 1 FROM rag_file_state WHERE repo=? AND file_path=?",
        ("demo-repo", "src/app.py"))
    assert row is not None


def test_sync_checkout_skips_when_lock_held(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "HOME", tmp_path / "home")
    _fresh_db()
    repo = _init_repo(tmp_path)

    def _raising_indexer(root):
        raise AssertionError("must not build an Indexer while locked")

    lock_path = gs._lock_path("demo-repo", "main")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    holder = open(lock_path, "w")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        monkeypatch.setattr(gs, "_build_indexer", _raising_indexer)
        result = gs.sync_checkout(str(repo), "a" * 40, "b" * 40, "1")
        assert result["skipped"] == "reindex already running"
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        holder.close()
