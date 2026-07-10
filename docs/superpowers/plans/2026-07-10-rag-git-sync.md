# RAG Git-Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep a repo's RAG index current automatically for the git events that
`post-commit` alone doesn't cover — pulls/merges and branch switches — with
zero configuration and no new indexing logic.

**Architecture:** One new module, `rag/git_sync.py`, is the single dispatch
point for three git hooks (`post-commit`, `post-merge`, `post-checkout`). It
resolves what changed for each event, takes a per-`(repo, branch)` lock so
overlapping triggers (e.g. an interactive rebase firing `post-commit`
repeatedly) can't each load the embedding model concurrently, then calls the
existing, unmodified `Indexer.incremental()`/`Indexer.full_index()`. Hook
scripts stay as thin, backgrounded (`nohup`) bash wrappers, exactly like
today's `post-commit`. `scripts/register_repo.py`'s existing idempotent
installer is generalized from one hook to three, so re-running
`claude-env onboard`/`register` on an already-onboarded repo is the upgrade
path.

**Tech Stack:** Python 3.13 (stdlib `fcntl`, `subprocess`, `pathlib`), bash
git hooks, pytest, the existing `rag/indexers/indexer.py::Indexer` class and
`sql/001_schema.sql`'s `rag_index_state`/`rag_file_state` tables (both
unchanged by this plan).

## Global Constraints

- **Never block or slow the git operation.** Every hook script backgrounds its
  work with `nohup ... &` and always `exit 0`, matching today's `post-commit`
  (`scripts/post-commit`).
- **No new indexing logic.** `rag/git_sync.py` only adds *triggers* — it calls
  `Indexer.incremental()`/`Indexer.full_index()` exactly as they exist today,
  unmodified.
- **Per-`(repo, branch)` lock**, path
  `$CLAUDE_ENV_HOME/state/locks/<repo>__<branch>.lock` (repo/branch names
  sanitized the same way `rag/retrievers/lance_store.py::table_name()`
  already does: `s.replace("/", "-").replace(" ", "_")`), acquired with
  `fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)`. If already held, skip
  immediately — before constructing anything that would load the embedding
  model — and return a `{"skipped": ...}` dict rather than raising.
- **`checkout` event is ignored unless `is_branch_flag == "1"`** (git's third
  `post-checkout` argument; `"0"` means a file-level checkout, not a branch
  switch).
- **The existing `--no-post-commit` CLI flag is kept by name** (it's
  documented in `docs/guide/onboarding.md`) but now gates all three hooks as
  one unit — it is not renamed and no new flag is introduced.
- **Never clobber a foreign (non-claude-env) git hook.** A hook file that
  exists and does not contain the literal string `"claude-env"` is left
  untouched; installation reports `"kept existing non-claude-env hook..."`.
- **Bare clones / worktrees / submodules are skipped**, same as today: if
  `<repo>/.git` doesn't exist, or exists as a file (not a directory), hook
  installation returns a `"skipped (...)"` string and writes nothing.

---

### Task 1: `rag/git_sync.py` — lock helper and module skeleton

**Files:**
- Create: `rag/git_sync.py`
- Test: `tests/test_git_sync.py`

**Interfaces:**
- Consumes: `rag.indexers.indexer._git(repo_root: str, *args: str) -> str`
  (existing helper, `rag/indexers/indexer.py:52`, reused as-is via import —
  do not duplicate this logic).
- Produces (used by Tasks 2 and 3):
  - `HOME: Path` — module-level, `Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))`.
  - `_lock_path(repo: str, branch: str) -> Path`
  - `_repo_lock(repo: str, branch: str)` — a context manager (via
    `@contextlib.contextmanager`) that yields `True` if the lock was
    acquired, `False` if another process already holds it. Always releases
    on exit.
  - `_current_branch(repo_root: str) -> str`
  - `_repo_slug(repo_root: str) -> str` — reads `.claude/repo-policy.yaml`'s
    `repo:` field directly (falls back to the directory basename if the file
    is missing or has no `repo:` key). Deliberately does **not** construct an
    `Indexer` — resolving the lock key must be cheap and must not depend on
    the same factory Tasks 2-3 gate behind the lock, or a held lock could
    never be tested without also tripping the Indexer construction it guards.
  - `_build_indexer(repo_root: str)` — factory seam; tests monkeypatch this
    to inject a model-free `Indexer` (same pattern as
    `tests/test_incremental_index.py::_indexer`). Called by Tasks 2-3 only
    *after* the lock is acquired.

- [ ] **Step 1: Write the failing tests for the lock helper**

Create `tests/test_git_sync.py` with this content:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_git_sync.py -v`
Expected: `ModuleNotFoundError: No module named 'rag.git_sync'` (the module
doesn't exist yet).

- [ ] **Step 3: Create `rag/git_sync.py`**

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_git_sync.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add rag/git_sync.py tests/test_git_sync.py
git commit -m "feat: add rag/git_sync.py lock helper and module skeleton"
```

---

### Task 2: `commit` and `merge` dispatch

**Files:**
- Modify: `rag/git_sync.py` (append to the file created in Task 1)
- Test: `tests/test_git_sync.py` (append)

**Interfaces:**
- Consumes: `_repo_lock`, `_current_branch`, `_repo_slug`, `_build_indexer`
  (Task 1). `Indexer.incremental(changed: list[str]) -> dict` (existing,
  `rag/indexers/indexer.py:192`, unmodified).
- Produces (used by Task 3 for `main()`'s final shape):
  - `sync_commit(repo_root: str, changed: list[str]) -> dict`
  - `sync_merge(repo_root: str, changed: list[str]) -> dict`
  - `main() -> int` (handles `commit`/`merge`; Task 3 extends it with
    `checkout`)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_git_sync.py`:

```python
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
```

This test proves the lock actually gates `_build_indexer` (and therefore any
embedding-model load): `_build_indexer` is patched to raise unconditionally,
so if `sync_commit` called it while the lock is held, the test would error
out instead of reaching the `assert`. The fact that the `assert` runs at all
is the proof.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_git_sync.py -v`
Expected: `AttributeError: module 'rag.git_sync' has no attribute 'sync_commit'`.

- [ ] **Step 3: Implement `sync_commit`, `sync_merge`, and `main()`**

Append to `rag/git_sync.py`, replacing the `if __name__ == "__main__": pass`
placeholder from Task 1:

```python
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


def main() -> int:
    try:
        args = sys.argv[1:]
        repo_root, event, rest = args[0], args[1], args[2:]
        if event == "commit":
            result = sync_commit(repo_root, rest)
        elif event == "merge":
            result = sync_merge(repo_root, rest)
        else:
            result = {"error": f"unknown event {event!r}"}
        print(result)
    except Exception as e:
        sys.stderr.write(f"[claude-env] git_sync error: {e}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`_build_indexer` is called exactly once, only inside the `with _repo_lock(...)`
block, only on the acquired path — this is what makes the lock-contention
test above valid: when the lock is already held, `_build_indexer` is never
reached, so a version of it that raises unconditionally proves the skip path
never touches the (expensive, model-loading) Indexer construction.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_git_sync.py -v`
Expected: 10 passed.

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -q`
Expected: all pass (no regressions in unrelated modules).

- [ ] **Step 6: Commit**

```bash
git add rag/git_sync.py tests/test_git_sync.py
git commit -m "feat: add commit/merge dispatch to rag/git_sync.py"
```

---

### Task 3: `checkout` dispatch (new-branch full index / known-branch catch-up)

**Files:**
- Modify: `rag/git_sync.py` (extend `main()`, add `sync_checkout`)
- Test: `tests/test_git_sync.py` (append)

**Interfaces:**
- Consumes: `_repo_lock`, `_current_branch`, `_repo_slug`, `_build_indexer`,
  `_git` (Tasks 1-2). `Indexer.full_index() -> dict` (existing,
  `rag/indexers/indexer.py:164`, unmodified). `Indexer.db.query_one(sql, params) -> dict | None`
  (existing `lib/db.py` interface, already used throughout the codebase).
  `rag_index_state` schema (existing, `sql/001_schema.sql`): columns
  `repo, branch, table_name, last_commit, chunk_count, embed_model, updated_at`.
- Produces: `sync_checkout(repo_root: str, prev_sha: str, new_sha: str, is_branch_flag: str) -> dict`;
  `main()`'s final three-event dispatch.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_git_sync.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_git_sync.py -v`
Expected: `AttributeError: module 'rag.git_sync' has no attribute 'sync_checkout'`.

- [ ] **Step 3: Implement `sync_checkout` and extend `main()`**

In `rag/git_sync.py`, add `sync_checkout` above `main()` and update `main()`'s
dispatch:

```python
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
        if row is None:
            return idx.full_index()
        out = _git(repo_root, "diff", "--name-only", row["last_commit"], new_sha)
        changed = [f for f in out.splitlines() if f]
        return idx.incremental(changed)
```

Replace `main()`'s dispatch body (from Task 2) with:

```python
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
    except Exception as e:
        sys.stderr.write(f"[claude-env] git_sync error: {e}\n")
    return 0
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_git_sync.py -v`
Expected: 14 passed.

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add rag/git_sync.py tests/test_git_sync.py
git commit -m "feat: add checkout dispatch to rag/git_sync.py"
```

---

### Task 4: Hook scripts — `post-commit` (updated), `post-merge` and `post-checkout` (new)

**Files:**
- Modify: `scripts/post-commit`
- Create: `scripts/post-merge`
- Create: `scripts/post-checkout`
- Test: `tests/test_hook_scripts.py`

**Interfaces:**
- Consumes: `rag/git_sync.py`'s CLI shape from Task 3
  (`python rag/git_sync.py <repo_root> {commit|merge|checkout} ...`).
- Produces: three files under `scripts/` that Task 5's installer copies
  verbatim into `<repo>/.git/hooks/`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_hook_scripts.py`:

```python
"""Static content checks for the git hook scripts that trigger RAG
re-indexing (scripts/post-commit, scripts/post-merge, scripts/post-checkout).

These don't execute the scripts (no real git working tree event to fire, and
the existing suite doesn't execute post-commit's bash either -- see
test_onboard_helpers.py, which tests the *installer*, and test_git_sync.py,
which tests the Python logic the scripts invoke). This asserts each script
dispatches to the right rag/git_sync.py event name, so a future edit can't
silently point post-merge at the wrong event without a test failing.
Run: pytest tests/ -q
"""
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (_ROOT / "scripts" / name).read_text()


def test_post_commit_dispatches_commit_event():
    text = _read("post-commit")
    assert "claude-env" in text
    assert "rag/git_sync.py" in text
    assert '"$REPO_ROOT" commit' in text
    assert "nohup" in text and "exit 0" in text


def test_post_merge_dispatches_merge_event():
    text = _read("post-merge")
    assert "claude-env" in text
    assert "rag/git_sync.py" in text
    assert '"$REPO_ROOT" merge' in text
    assert "ORIG_HEAD" in text
    assert "nohup" in text and "exit 0" in text


def test_post_checkout_dispatches_checkout_event():
    text = _read("post-checkout")
    assert "claude-env" in text
    assert "rag/git_sync.py" in text
    assert '"$REPO_ROOT" checkout "$1" "$2" "$3"' in text
    assert "nohup" in text and "exit 0" in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_hook_scripts.py -v`
Expected: `post-merge`/`post-checkout` tests fail with `FileNotFoundError`;
`post-commit` test fails on the `'"$REPO_ROOT" commit'` assertion (today's
script dispatches to `incremental_index.py`, not `git_sync.py commit`).

- [ ] **Step 3: Update `scripts/post-commit`**

Replace the file's full contents with:

```bash
#!/usr/bin/env bash
# claude-env :: git post-commit hook -> RAG git-sync (incremental re-index).
#
# Install per repo:
#   ln -sf ~/.claude-env/scripts/post-commit .git/hooks/post-commit
#   chmod +x .git/hooks/post-commit
#
# Uses the platform venv Python ($CLAUDE_ENV_HOME/venv/bin/python) so all RAG
# dependencies are available without requiring manual venv activation.
# Runs in the background so commits stay fast; output appended to logs/incremental_index.log.

set -euo pipefail
REPO_ROOT="$(git rev-parse --show-toplevel)"
CLAUDE_ENV_HOME="${CLAUDE_ENV_HOME:-$HOME/.claude-env}"
VENV_PY="${CLAUDE_ENV_HOME}/venv/bin/python"

if [[ ! -x "$VENV_PY" ]]; then
  echo "[claude-env] venv not found at $VENV_PY — run bootstrap.py first" >&2
  exit 0   # non-fatal: don't block the commit
fi

# Files changed in HEAD
CHANGED=$(git diff-tree --no-commit-id --name-only -r HEAD || true)
[[ -z "$CHANGED" ]] && exit 0

mkdir -p "${CLAUDE_ENV_HOME}/logs"

# shellcheck disable=SC2086
nohup "$VENV_PY" "${CLAUDE_ENV_HOME}/rag/git_sync.py" "$REPO_ROOT" commit $CHANGED \
  >> "${CLAUDE_ENV_HOME}/logs/incremental_index.log" 2>&1 &
exit 0
```

(Only the dispatch target changed from `rag/incremental_index.py` to
`rag/git_sync.py ... commit`; every other line is byte-identical to today's
script. `rag/incremental_index.py` itself is untouched and keeps working
standalone for `claude-env reindex`.)

- [ ] **Step 4: Create `scripts/post-merge`**

```bash
#!/usr/bin/env bash
# claude-env :: git post-merge hook -> RAG git-sync (incremental re-index).
#
# Fires after any merge, including a `git pull`'s fast-forward -- this is
# what post-commit alone misses, since a fast-forward merge doesn't invoke
# `git commit`.
#
# Install per repo:
#   ln -sf ~/.claude-env/scripts/post-merge .git/hooks/post-merge
#   chmod +x .git/hooks/post-merge
#
# Uses the platform venv Python ($CLAUDE_ENV_HOME/venv/bin/python) so all RAG
# dependencies are available without requiring manual venv activation.
# Runs in the background so the merge/pull stays fast; output appended to
# logs/incremental_index.log.

set -euo pipefail
REPO_ROOT="$(git rev-parse --show-toplevel)"
CLAUDE_ENV_HOME="${CLAUDE_ENV_HOME:-$HOME/.claude-env}"
VENV_PY="${CLAUDE_ENV_HOME}/venv/bin/python"

if [[ ! -x "$VENV_PY" ]]; then
  echo "[claude-env] venv not found at $VENV_PY — run bootstrap.py first" >&2
  exit 0   # non-fatal: don't block the merge
fi

# git sets ORIG_HEAD before any merge (including a pull's fast-forward), so
# this always resolves even on the very first merge in a repo's history.
CHANGED=$(git -C "$REPO_ROOT" diff --name-only ORIG_HEAD HEAD || true)
[[ -z "$CHANGED" ]] && exit 0

mkdir -p "${CLAUDE_ENV_HOME}/logs"

# shellcheck disable=SC2086
nohup "$VENV_PY" "${CLAUDE_ENV_HOME}/rag/git_sync.py" "$REPO_ROOT" merge $CHANGED \
  >> "${CLAUDE_ENV_HOME}/logs/incremental_index.log" 2>&1 &
exit 0
```

- [ ] **Step 5: Create `scripts/post-checkout`**

```bash
#!/usr/bin/env bash
# claude-env :: git post-checkout hook -> RAG git-sync (branch-aware re-index).
#
# git passes three arguments: <prev_head_sha> <new_head_sha> <is_branch_flag>
# ("1" for a real branch switch, "0" for a plain file-level checkout, which
# this hook ignores). rag/git_sync.py decides whether the new branch needs a
# full index (never indexed before) or an incremental catch-up.
#
# Install per repo:
#   ln -sf ~/.claude-env/scripts/post-checkout .git/hooks/post-checkout
#   chmod +x .git/hooks/post-checkout
#
# Uses the platform venv Python ($CLAUDE_ENV_HOME/venv/bin/python) so all RAG
# dependencies are available without requiring manual venv activation.
# Runs in the background so the checkout stays fast; output appended to
# logs/incremental_index.log.

set -euo pipefail
REPO_ROOT="$(git rev-parse --show-toplevel)"
CLAUDE_ENV_HOME="${CLAUDE_ENV_HOME:-$HOME/.claude-env}"
VENV_PY="${CLAUDE_ENV_HOME}/venv/bin/python"

if [[ ! -x "$VENV_PY" ]]; then
  echo "[claude-env] venv not found at $VENV_PY — run bootstrap.py first" >&2
  exit 0   # non-fatal: don't block the checkout
fi

mkdir -p "${CLAUDE_ENV_HOME}/logs"

nohup "$VENV_PY" "${CLAUDE_ENV_HOME}/rag/git_sync.py" "$REPO_ROOT" checkout "$1" "$2" "$3" \
  >> "${CLAUDE_ENV_HOME}/logs/incremental_index.log" 2>&1 &
exit 0
```

- [ ] **Step 6: Make the new scripts executable**

Run: `chmod +x scripts/post-merge scripts/post-checkout`
Expected: no output. (`scripts/post-commit` is already executable from
before this plan.)

- [ ] **Step 7: Run the tests to verify they pass**

Run: `pytest tests/test_hook_scripts.py -v`
Expected: 3 passed.

- [ ] **Step 8: Run the full suite**

Run: `pytest tests/ -q`
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add scripts/post-commit scripts/post-merge scripts/post-checkout tests/test_hook_scripts.py
git commit -m "feat: add post-merge/post-checkout RAG git-sync hooks"
```

---

### Task 5: `scripts/register_repo.py` — install/upgrade all three hooks

**Files:**
- Modify: `scripts/register_repo.py:365-391` (the `_install_post_commit`
  function) and its call site at `scripts/register_repo.py:609-612`
  (identify it by the `# 4. git post-commit hook` comment, since editing the
  function above shifts every later line number by a few lines).
- Modify: `docs/guide/onboarding.md:45`, `docs/guide/onboarding.md:227-230`,
  `docs/guide/onboarding.md:298-300`
- Test: `tests/test_onboard_helpers.py:28-47` (the three
  `test_post_commit_*` tests — made table-driven over the three hook names)

**Interfaces:**
- Consumes: nothing from Tasks 1-4 directly (this task only installs the
  files Task 4 created; it does not import `rag/git_sync.py`).
- Produces: `_install_git_hooks(repo_root: str, dry_run: bool) -> dict[str, str]`
  (replaces `_install_post_commit`, which is deleted) — maps each of
  `"post-commit"`, `"post-merge"`, `"post-checkout"` to its own status
  string (`"installed"` / `"already installed"` /
  `"kept existing non-claude-env hook..."` / `"skipped (...)"`).

- [ ] **Step 1: Write the failing tests**

In `tests/test_onboard_helpers.py`, replace the three existing tests
(`test_post_commit_install_and_idempotency`,
`test_post_commit_does_not_clobber_foreign_hook`,
`test_post_commit_skips_non_git`, currently at lines 28-47) with:

```python
_HOOK_NAMES = ("post-commit", "post-merge", "post-checkout")


def test_git_hooks_install_and_idempotency(tmp_path):
    repo = _git_repo(tmp_path)
    statuses = reg._install_git_hooks(repo, dry_run=False)
    assert set(statuses) == set(_HOOK_NAMES)
    for name in _HOOK_NAMES:
        assert statuses[name] == "installed"
        hook = Path(repo) / ".git" / "hooks" / name
        assert hook.exists() and (hook.stat().st_mode & 0o111)   # executable
    # second run is a no-op (same content) for every hook
    statuses2 = reg._install_git_hooks(repo, dry_run=False)
    assert all(v == "already installed" for v in statuses2.values())


def test_git_hooks_do_not_clobber_foreign_hooks(tmp_path):
    repo = _git_repo(tmp_path)
    hook = Path(repo) / ".git" / "hooks" / "post-merge"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho mine\n")
    statuses = reg._install_git_hooks(repo, dry_run=False)
    assert "kept existing" in statuses["post-merge"]
    assert hook.read_text() == "#!/bin/sh\necho mine\n"          # untouched
    # the other two, with no foreign hook in the way, still install normally
    assert statuses["post-commit"] == "installed"
    assert statuses["post-checkout"] == "installed"


def test_git_hooks_skip_non_git(tmp_path):
    statuses = reg._install_git_hooks(str(tmp_path), dry_run=False)
    assert all("not a git repo" in v for v in statuses.values())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_onboard_helpers.py -v`
Expected: `AttributeError: module 'register_repo' has no attribute '_install_git_hooks'`.

- [ ] **Step 3: Replace `_install_post_commit` with `_install_git_hooks`**

In `scripts/register_repo.py`, replace the entire `_install_post_commit`
function (lines 365-391) with:

```python
def _install_git_hooks(repo_root: str, dry_run: bool) -> dict[str, str]:
    """Install scripts/{post-commit,post-merge,post-checkout} into
    <repo>/.git/hooks so commits, merges/pulls, and branch switches all
    trigger a RAG re-index (rag/git_sync.py). Idempotent per hook; never
    clobbers a foreign (non-claude-env) hook of the same name."""
    hook_names = ("post-commit", "post-merge", "post-checkout")
    git_dir = Path(repo_root) / ".git"
    if not git_dir.exists():
        return {name: "skipped (not a git repo)" for name in hook_names}
    if git_dir.is_file():                      # worktree/submodule: .git is a file
        return {name: "skipped (.git is a file — worktree/submodule)"
                for name in hook_names}

    statuses: dict[str, str] = {}
    marker = "claude-env"                       # our hooks carry this in a comment
    for name in hook_names:
        src = _HERE / "scripts" / name
        if not src.exists():
            statuses[name] = f"skipped ({name} script missing)"
            continue
        hooks = git_dir / "hooks"
        dst = hooks / name
        if dst.exists():
            try:
                existing = dst.read_text(errors="ignore")
            except Exception:
                existing = ""
            if marker not in existing:
                statuses[name] = "kept existing non-claude-env hook (install manually if wanted)"
                continue
            if existing == src.read_text():
                statuses[name] = "already installed"
                continue
        if not dry_run:
            hooks.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            dst.chmod(0o755)
        statuses[name] = "installed"
    return statuses
```

- [ ] **Step 4: Update the call site**

Find the comment `# 4. git post-commit hook -> incremental RAG re-index on
every commit` (currently `scripts/register_repo.py:609-612`) and replace:

```python
    # 4. git post-commit hook -> incremental RAG re-index on every commit
    tag = f"{YELLOW}DRY RUN{RESET} " if args.dry_run else ""
    if not args.no_post_commit:
        print(f"\n{tag}post-commit hook: {_install_post_commit(repo_root, args.dry_run)}")
```

with:

```python
    # 4. git hooks (commit/merge/checkout) -> RAG git-sync re-index
    tag = f"{YELLOW}DRY RUN{RESET} " if args.dry_run else ""
    if not args.no_post_commit:
        hook_statuses = _install_git_hooks(repo_root, args.dry_run)
        for name, status in hook_statuses.items():
            print(f"\n{tag}{name} hook: {status}")
```

- [ ] **Step 5: Update the flag's help text**

Find (currently near `scripts/register_repo.py:559-560`):

```python
    parser.add_argument("--no-post-commit", action="store_true",
                        help="Skip installing the git post-commit RAG re-index hook")
```

Replace with:

```python
    parser.add_argument("--no-post-commit", action="store_true",
                        help="Skip installing the git post-commit/post-merge/"
                             "post-checkout RAG re-index hooks")
```

(The flag's name is unchanged — it is a documented public CLI flag — only
its help text and scope broaden to describe all three hooks it now gates.)

- [ ] **Step 6: Update `docs/guide/onboarding.md`**

Replace the flag table row (line 45):

```
| `--no-post-commit` | flag | off | Skips installing the git `post-commit` hook. |
```

with:

```
| `--no-post-commit` | flag | off | Skips installing all three git hooks (`post-commit`/`post-merge`/`post-checkout`) that trigger RAG re-indexing. |
```

Replace the "Idempotent extras" bullet (lines 227-230):

```
- **`.git/hooks/post-commit`** (`_install_post_commit`) — copies
  `scripts/post-commit` into the target repo's git hooks dir, `chmod 0o755`.
  Skipped if `.git` doesn't exist or is a file (worktree/submodule). If a
  `post-commit` hook already exists and does **not** contain the literal
  string `"claude-env"`, it's left alone (`"kept existing non-claude-env
  hook"`) — the script never silently overwrites a foreign hook.
```

with:

```
- **`.git/hooks/{post-commit,post-merge,post-checkout}`**
  (`_install_git_hooks`) — copies `scripts/post-commit`, `scripts/post-merge`,
  and `scripts/post-checkout` into the target repo's git hooks dir, each
  `chmod 0o755`, so commits, merges/pulls, and branch switches all keep the
  RAG index current via `rag/git_sync.py`. Skipped (for all three) if `.git`
  doesn't exist or is a file (worktree/submodule). Each hook is installed
  independently: if e.g. a `post-merge` hook already exists and does **not**
  contain the literal string `"claude-env"`, only that one is left alone
  (`"kept existing non-claude-env hook"`) — the others still install
  normally, and the script never silently overwrites a foreign hook.
```

Replace the test-coverage table rows (lines 298-300):

```
| `test_post_commit_install_and_idempotency` | First install returns `"installed"` and the hook file is executable (`st_mode & 0o111`); a second run on identical content returns `"already installed"`. |
| `test_post_commit_does_not_clobber_foreign_hook` | A pre-existing hook without the `claude-env` marker is left byte-for-byte untouched, status contains `"kept existing"`. |
| `test_post_commit_skips_non_git` | A non-git directory returns a status containing `"not a git repo"`. |
```

with:

```
| `test_git_hooks_install_and_idempotency` | First install returns `"installed"` for all three hooks (`post-commit`/`post-merge`/`post-checkout`), each executable (`st_mode & 0o111`); a second run on identical content returns `"already installed"` for all three. |
| `test_git_hooks_do_not_clobber_foreign_hooks` | A pre-existing `post-merge` hook without the `claude-env` marker is left byte-for-byte untouched (status contains `"kept existing"`), while `post-commit`/`post-checkout` still install normally. |
| `test_git_hooks_skip_non_git` | A non-git directory returns a status containing `"not a git repo"` for all three hooks. |
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `pytest tests/test_onboard_helpers.py -v`
Expected: all pass, including the three new/renamed tests.

- [ ] **Step 8: Run the full suite**

Run: `pytest tests/ -q`
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add scripts/register_repo.py tests/test_onboard_helpers.py docs/guide/onboarding.md
git commit -m "feat: install post-merge/post-checkout hooks at onboarding time"
```

---

## Manual verification (after all 5 tasks)

Not a substitute for the automated tests above, but confirms the real git
hooks fire end to end in a disposable throwaway repo (do not run this against
a real onboarded repo without asking first, per this platform's own
`CLAUDE.md`):

```bash
T=$(mktemp -d)
git init -q -b main "$T"
cd "$T"
mkdir .claude
cat > .claude/repo-policy.yaml <<'EOF'
repo: manual-check
tier: 0
rag:
  enabled: true
  index_paths: ['**']
EOF
echo "print('hi')" > app.py
git add -A && git commit -q -m init

python3 /path/to/claude-env-platform/scripts/register_repo.py "$T" --yes --no-template
ls -la .git/hooks/post-commit .git/hooks/post-merge .git/hooks/post-checkout

echo "print('bye')" >> app.py
git add -A && git commit -q -m "second commit"
sleep 1
cat "${CLAUDE_ENV_HOME:-$HOME/.claude-env}/logs/incremental_index.log" | tail -5
```

Expected: all three hook files exist and are executable; the log shows a
`commit` event ran after the second commit.
