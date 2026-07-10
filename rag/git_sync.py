#!/usr/bin/env python3
"""
claude-env :: unified RAG git-sync dispatch
File: rag/git_sync.py
Purpose:
    Single entry point for the three git hooks that keep a repo's RAG index
    current: post-commit, post-merge, post-checkout. Reuses the existing
    Indexer.incremental()/full_index() methods unmodified -- this module
    only adds new *triggers*, no new indexing logic.

    A per-(repo, branch) lock file prevents overlapping re-index runs (e.g.
    an interactive rebase firing post-commit many times in a few seconds)
    from each loading the embedding model concurrently.

Usage (invoked by the hook scripts in scripts/, never run by hand):
    python rag/git_sync.py <repo_root> commit   <file1> <file2> ...
    python rag/git_sync.py <repo_root> merge    <file1> <file2> ...
    python rag/git_sync.py <repo_root> checkout <prev_sha> <new_sha> <is_branch_flag>

Never raises to the caller: every path is caught and logged so a hook script
can never fail or slow down the git operation that triggered it.
"""
from __future__ import annotations

import fcntl
import os
import sys
import traceback
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rag.indexers.indexer import _git  # noqa: E402 -- shared git helper, not duplicated

HOME = Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))


def _current_branch(repo_root: str) -> str:
    return _git(repo_root, "rev-parse", "--abbrev-ref", "HEAD") or "main"


def _is_ancestor(repo_root: str, old_sha: str, new_sha: str) -> bool:
    """True if old_sha is still in new_sha's history. False after a
    rebase/force-push drops it -- in that case a diff against old_sha would
    fail and silently produce an empty changed-file list (since _git()
    discards the exit code), while Indexer.incremental() still advances
    rag_index_state.last_commit to new_sha, leaving the index stale with no
    way to self-heal on a later checkout. Callers must fall back to a full
    re-index when this returns False."""
    import subprocess
    r = subprocess.run(["git", "-C", repo_root, "merge-base", "--is-ancestor", old_sha, new_sha])
    return r.returncode == 0


def _lock_path(repo: str, branch: str) -> Path:
    safe = lambda s: s.replace("/", "-").replace(" ", "_")
    return HOME / "state" / "locks" / f"{safe(repo)}__{safe(branch)}.lock"


@contextmanager
def _repo_lock(repo: str, branch: str):
    """Non-blocking per-(repo, branch) lock. Yields True if acquired, False
    if another process already holds it -- caller should skip its work
    rather than proceed. Always releases the lock on exit."""
    path = _lock_path(repo, branch)
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "w")
    acquired = False
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        acquired = True
        yield True
    except OSError:
        yield False
    finally:
        if acquired:
            fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def _repo_slug(repo_root: str) -> str:
    """Cheap slug resolution for the lock key -- reads .claude/repo-policy.yaml
    directly rather than constructing a full Indexer (which also spins up a
    PolicyEngine, AuditLogger, and DB connection). Falls back to the
    directory basename if the policy file is missing or has no repo: key."""
    import yaml
    pol = Path(repo_root) / ".claude" / "repo-policy.yaml"
    if pol.exists():
        doc = yaml.safe_load(pol.read_text()) or {}
        slug = doc.get("repo")
        if slug:
            return slug
    return Path(repo_root).name


def _build_indexer(repo_root: str):
    """Factory seam: tests monkeypatch this to inject a model-free Indexer
    (see tests/test_incremental_index.py::_indexer for the pattern). Callers
    must only invoke this after acquiring the repo/branch lock -- constructing
    an Indexer is cheap on its own, but the methods called on it
    (.incremental()/.full_index()) load the embedding model, which is not."""
    from rag.indexers.indexer import Indexer
    return Indexer(repo_root)


def sync_commit(repo_root: str, changed: list[str]) -> dict:
    branch = _current_branch(repo_root)
    repo_slug = _repo_slug(repo_root)
    with _repo_lock(repo_slug, branch) as acquired:
        if not acquired:
            return {"skipped": "reindex already running",
                    "repo": repo_slug, "branch": branch}
        return _build_indexer(repo_root).incremental(changed)


def sync_merge(repo_root: str, changed: list[str]) -> dict:
    return sync_commit(repo_root, changed)   # identical strategy


def sync_checkout(repo_root: str, prev_sha: str, new_sha: str, is_branch_flag: str) -> dict:
    if is_branch_flag != "1":
        return {"skipped": "file-level checkout, not a branch switch"}
    branch = _current_branch(repo_root)
    repo_slug = _repo_slug(repo_root)
    with _repo_lock(repo_slug, branch) as acquired:
        if not acquired:
            return {"skipped": "reindex already running",
                    "repo": repo_slug, "branch": branch}
        idx = _build_indexer(repo_root)
        row = idx.db.query_one(
            "SELECT last_commit FROM rag_index_state WHERE repo=? AND branch=?",
            (repo_slug, branch))
        if row is None or not _is_ancestor(repo_root, row["last_commit"], new_sha):
            return idx.full_index()
        out = _git(repo_root, "diff", "--name-only", row["last_commit"], new_sha)
        changed = [f for f in out.splitlines() if f]
        return idx.incremental(changed)


def main() -> int:
    try:
        args = sys.argv[1:]
        repo_root, event, rest = args[0], args[1], args[2:]
        if event == "commit":
            result = sync_commit(repo_root, rest)
        elif event == "merge":
            result = sync_merge(repo_root, rest)
        elif event == "checkout":
            result = sync_checkout(repo_root, rest[0], rest[1], rest[2])
        else:
            result = {"error": f"unknown event {event!r}"}
        print(result)
    except Exception:
        sys.stderr.write(f"[claude-env] git_sync error:\n{traceback.format_exc()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
