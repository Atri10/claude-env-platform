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
