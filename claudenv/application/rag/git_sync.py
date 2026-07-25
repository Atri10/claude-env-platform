"""claude-env :: Application - RAG - Git hook sync dispatcher

Single entry point for the three git hooks that keep a repo's RAG index
current: post-commit, post-merge, post-checkout. Reuses the hexagonal
``RagIndexer.incremental_index()`` / ``RagIndexer.full_index()`` application
use cases unmodified -- this module only adds the git-hook *triggers* and
the disk-reading orchestration the pure indexer deliberately omits.

A per-(repo, branch) lock file prevents overlapping re-index runs (e.g. an
interactive rebase firing post-commit many times in a few seconds) from each
loading the embedding model concurrently.

Usage (invoked by the hook scripts in scripts/, never run by hand)::

    python -m claudenv.application.rag.git_sync <repo_root> commit   <file1> <file2> ...
    python -m claudenv.application.rag.git_sync <repo_root> merge    <file1> <file2> ...
    python -m claudenv.application.rag.git_sync <repo_root> checkout <prev_sha> <new_sha> <is_branch_flag>

Never raises to the caller: every path is caught and logged so a hook script
can never fail or slow down the git operation that triggered it.
"""
from __future__ import annotations

import fcntl
import logging
import subprocess
import sys
import traceback
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

from claudenv.adapters.config import get_claude_env_home_provider, get_config
from claudenv.adapters.embedding import get_embedder
from claudenv.adapters.persistence import SQLiteDatabase, SQLiteRagBookkeeping
from claudenv.adapters.vector.lancedb import LanceDbVectorStore
from claudenv.application.rag.indexer import RagIndexer
from claudenv.domain.rag import BranchName, RepoSlug
from claudenv.ports import IRagBookkeeping

logger = logging.getLogger(__name__)

# Vendor/binary directories skipped during a full-disk walk (mirrors
# RagService.index_repo's _SKIP_DIRS so a checkout-triggered full re-index
# indexes the same corpus the MCP server would).
_SKIP_DIRS = {
    ".git", "node_modules", "venv", ".venv", "__pycache__",
    ".claude-env", "dist", "build",
}
# Files above this size, or that look binary (NUL byte in the first 8 KiB),
# are skipped: embedding a decoded PNG/binary yields hundreds of meaningless
# chunks that pollute RAG. Same threshold as RagService.index_repo.
_MAX_FILE_BYTES = 500 * 1024 * 1024

# Per-(repo, branch) lock directory. Tests monkeypatch this to a temp dir;
# ``None`` defers to the configured claude-env home (see ``_home()``).
HOME: Path | None = None


def _home() -> Path:
    """Resolve the claude-env home used for lock files. Falls back to the
    configured provider so a bare ``import`` of this module never touches the
    filesystem unless a sync actually runs."""
    if HOME is not None:
        return HOME
    return Path(get_claude_env_home_provider().get_claude_env_home())


def _git(repo_root: str, *args: str) -> str:
    """Run a git command in ``repo_root`` and return stripped stdout. Exit
    code is discarded (a failed diff yields an empty string, not an
    exception) so callers can treat "no output" as "no changed files"."""
    return subprocess.run(
        ["git", "-C", repo_root, *args],
        capture_output=True, text=True,
    ).stdout.strip()


def _current_branch(repo_root: str) -> str:
    return _git(repo_root, "rev-parse", "--abbrev-ref", "HEAD") or "main"


def _is_ancestor(repo_root: str, old_sha: str, new_sha: str) -> bool:
    """True if ``old_sha`` is still in ``new_sha``'s history. False after a
    rebase/force-push drops it -- in that case a diff against ``old_sha``
    would fail and silently produce an empty changed-file list (since
    ``_git()`` discards the exit code), while ``incremental_index()`` still
    advances ``rag_index_state.last_commit`` to ``new_sha``, leaving the
    index stale with no way to self-heal on a later checkout. Callers must
    fall back to a full re-index when this returns False."""
    r = subprocess.run(
        ["git", "-C", repo_root, "merge-base", "--is-ancestor", old_sha, new_sha],
        capture_output=True,
    )
    return r.returncode == 0


def _lock_path(repo: str, branch: str) -> Path:
    safe = lambda s: s.replace("/", "-").replace(" ", "_")
    return _home() / "state" / "locks" / f"{safe(repo)}__{safe(branch)}.lock"


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
    """Cheap slug resolution for the lock key -- reads
    ``.claude/repo-policy.yaml`` directly rather than constructing a full
    indexer (which also spins up a policy engine, audit logger, and DB
    connection). Falls back to the directory basename if the policy file is
    missing or has no ``repo:`` key."""
    import yaml

    pol = Path(repo_root) / ".claude" / "repo-policy.yaml"
    if pol.exists():
        doc = yaml.safe_load(pol.read_text()) or {}
        slug = doc.get("repo")
        if slug:
            return str(slug)
    return Path(repo_root).name


def _default_indexer(repo_root: str, branch: str) -> RagIndexer:
    """Production factory: build a repo-scoped ``RagIndexer`` from the
    adapter factories the same way ``LanceDbRagServer.create_server`` does.
    Constructing an indexer is cheap on its own, but the methods called on
    it (``incremental_index``/``full_index``) load the embedding model, which
    is not -- so callers must only invoke this after acquiring the
    repo/branch lock. Tests inject a fake indexer through ``GitSync``'s
    ``indexer=`` seam instead of calling this."""
    config = get_config()
    repo_v = RepoSlug.from_string(_repo_slug(repo_root))
    branch_v = BranchName.from_string(branch)
    db = SQLiteDatabase(config.get_database_dsn())
    rag_config = config.get_rag_config()
    bookkeeping = SQLiteRagBookkeeping(db)
    store = LanceDbVectorStore(config.get_lancedb_path(), rag_config.embedding_dim)
    embedder = get_embedder(rag_config)
    return RagIndexer(
        repo=repo_v, branch=branch_v, store=store,
        bookkeeping=bookkeeping, embedder=embedder, chunker=rag_config,
    )


class GitSync:
    """Git-hook dispatcher for incremental RAG reindexing.

    Orchestrates the disk I/O the pure ``RagIndexer`` omits: reads changed
    files (or the full committed-file set for a checkout full-reindex),
    skips vendor dirs / binaries / oversized files, and delegates to
    ``RagIndexer.incremental_index()`` / ``RagIndexer.full_index()``.
    """

    def __init__(
            self,
            repo_root: str,
            *,
            indexer: RagIndexer | None = None,
            bookkeeping: IRagBookkeeping | None = None,
    ) -> None:
        self.repo_root = str(Path(repo_root).resolve())
        self._indexer = indexer
        self._bookkeeping = bookkeeping

    def _ensure_indexer(self) -> RagIndexer:
        if self._indexer is None:
            self._indexer = _default_indexer(
                self.repo_root, _current_branch(self.repo_root))
        return self._indexer

    def _get_bookkeeping(self) -> IRagBookkeeping:
        if self._bookkeeping is not None:
            return self._bookkeeping
        return self._ensure_indexer().bookkeeping

    def _read_text(self, rel: str) -> str | None:
        """Read a tracked file's text, or None if it is deleted / binary /
        oversized. Mirrors the size + NUL-byte guards in
        ``RagService.index_repo``."""
        path = Path(self.repo_root) / rel
        if not path.exists():
            return None
        try:
            data = path.read_bytes()
        except Exception:
            logger.warning("git_sync: skipped unreadable file %s", rel, exc_info=True)
            return None
        if b"\x00" in data[:8192] or len(data) > _MAX_FILE_BYTES:
            return None
        return data.decode("utf-8", "ignore")

    def _changed_dict(self, changed: list[str]) -> dict[str, str | None]:
        """Build the ``{path: text | None}`` map ``incremental_index``
        expects from a list of changed paths (None marks a deletion)."""
        out: dict[str, str | None] = {}
        for rel in changed:
            text = self._read_text(rel)
            if text is None and not (Path(self.repo_root) / rel).exists():
                out[rel] = None  # file removed -> drop chunks + bookkeeping
            elif text is not None:
                out[rel] = text
            # binary/oversized + still-present: skip silently
        return out

    def _committed_files(self) -> dict[str, str]:
        """Walk the repo's git-tracked (committed) files into a ``{path:
        text}`` map for ``full_index()``. Uses ``git ls-files`` (not rglob)
        so only committed sources are indexed -- matching master's
        ``index_only_committed=True`` default for git-hook triggers."""
        files: dict[str, str] = {}
        for rel in _git(self.repo_root, "ls-files").splitlines():
            if not rel:
                continue
            if any(part in _SKIP_DIRS for part in Path(rel).parts):
                continue
            text = self._read_text(rel)
            if text is not None:
                files[rel] = text
        return files

    def _stamp_commit(self, repo: RepoSlug, branch: BranchName, commit: str) -> None:
        """``RagIndexer.full_index()`` records ``last_commit='0'`` (it has
        no git context); after a checkout-triggered full re-index we know the
        real HEAD, so stamp it onto the index state so the next checkout can
        diff against it instead of always falling back to a full re-index."""
        bk = self._get_bookkeeping()
        state = bk.get_index_state(repo, branch)
        if state is None:
            return
        bk.set_index_state(replace(state, last_commit=commit))

    def sync_commit(self, changed: list[str]) -> dict[str, Any]:
        idx = self._ensure_indexer()
        commit = _git(self.repo_root, "rev-parse", "HEAD") or "0"
        return idx.incremental_index(
            idx.repo, idx.branch, commit, self._changed_dict(changed))

    def sync_merge(self, changed: list[str]) -> dict[str, Any]:
        # Identical strategy to commit: a merge is just a set of changed files.
        return self.sync_commit(changed)

    def sync_checkout(
            self, prev_sha: str, new_sha: str, is_branch_flag: str,
    ) -> dict[str, Any]:
        if is_branch_flag != "1":
            return {"skipped": "file-level checkout, not a branch switch"}
        idx = self._ensure_indexer()
        bk = self._get_bookkeeping()
        state = bk.get_index_state(idx.repo, idx.branch)
        last = state.last_commit if state else None
        if last is None or not _is_ancestor(self.repo_root, last, new_sha):
            result = idx.full_index(idx.repo, idx.branch, self._committed_files())
            self._stamp_commit(idx.repo, idx.branch, new_sha)
            return result
        changed = _git(self.repo_root, "diff", "--name-only", last, new_sha)
        return idx.incremental_index(
            idx.repo, idx.branch, new_sha,
            self._changed_dict([f for f in changed.splitlines() if f]))


def _build_indexer(repo_root: str) -> GitSync:
    """Factory seam: tests monkeypatch this to inject a model-free
    ``GitSync`` (see ``GitSync(indexer=...)``). Production builds a real
    indexer lazily, only after the repo/branch lock is acquired."""
    return GitSync(repo_root)


def sync_commit(repo_root: str, changed: list[str]) -> dict[str, Any]:
    branch = _current_branch(repo_root)
    repo_slug = _repo_slug(repo_root)
    with _repo_lock(repo_slug, branch) as acquired:
        if not acquired:
            return {"skipped": "reindex already running",
                    "repo": repo_slug, "branch": branch}
        return _build_indexer(repo_root).sync_commit(changed)


def sync_merge(repo_root: str, changed: list[str]) -> dict[str, Any]:
    branch = _current_branch(repo_root)
    repo_slug = _repo_slug(repo_root)
    with _repo_lock(repo_slug, branch) as acquired:
        if not acquired:
            return {"skipped": "reindex already running",
                    "repo": repo_slug, "branch": branch}
        return _build_indexer(repo_root).sync_merge(changed)


def sync_checkout(
        repo_root: str, prev_sha: str, new_sha: str, is_branch_flag: str,
) -> dict[str, Any]:
    if is_branch_flag != "1":
        return {"skipped": "file-level checkout, not a branch switch"}
    branch = _current_branch(repo_root)
    repo_slug = _repo_slug(repo_root)
    with _repo_lock(repo_slug, branch) as acquired:
        if not acquired:
            return {"skipped": "reindex already running",
                    "repo": repo_slug, "branch": branch}
        return _build_indexer(repo_root).sync_checkout(prev_sha, new_sha, is_branch_flag)


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
