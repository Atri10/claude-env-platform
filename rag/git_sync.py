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
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rag.indexers.indexer import _git  # noqa: E402 -- shared git helper, not duplicated

HOME = Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))


def _current_branch(repo_root: str) -> str:
    return _git(repo_root, "rev-parse", "--abbrev-ref", "HEAD") or "main"


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


if __name__ == "__main__":
    pass  # main() dispatch added in Task 2
