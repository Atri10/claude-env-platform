# RAG Git-Sync (keeping the index current) — Design

## Problem

`Indexer.incremental()`/`full_index()`/`index_branch()` already exist and do the
real work of keeping a repo's RAG index current, but only one trigger reaches
them today: the `post-commit` git hook, installed at onboarding, which
incrementally re-indexes the files changed in *that* commit.

Three common ways a repo's tracked content changes are **not** covered by that
trigger, so the index silently goes stale until someone remembers to run
`claude-env reindex`/`claude-env index` by hand:

1. **Remote changes** — `git pull` (a fast-forward merge, or any merge) does
   not invoke `git commit`, so `post-commit` never fires.
2. **New branches / branch switches** — each `(repo, branch)` gets its own
   LanceDB table (`rag_index_state`, `table_name()`), but nothing indexes a
   branch the first time you check it out, and nothing catches it back up to
   the commits made on it since it was last indexed.
3. **Already-onboarded repos** only have `post-commit` installed; there is no
   upgrade path to add new hook types without re-onboarding.

Uncommitted-but-unsaved-to-git changes are explicitly **out of scope** — RAG
scope is "committed files" by design (`Indexer._candidate_files()`), and this
project keeps that: freshness is defined relative to git state, not the
working tree's uncommitted edits.

## Goal

Make RAG freshness fully automatic and zero-config for the three git events
above, reusing the existing `Indexer` methods — no new indexing logic, only
new *triggers* for the logic that already exists.

## Design

### 1. `rag/git_sync.py` (new) — single dispatch point for all three triggers

```
python rag/git_sync.py <repo_root> commit   <file1> <file2> ...
python rag/git_sync.py <repo_root> merge    <file1> <file2> ...
python rag/git_sync.py <repo_root> checkout <prev_sha> <new_sha> <is_branch_flag>
```

`commit` and `merge` take an already-computed file list (the hook script
computes it with plain `git diff`/`git diff-tree`, exactly as today's
`post-commit` already does — see below); `checkout` takes raw SHAs because
its strategy decision needs a `rag_index_state` lookup that only happens
inside Python. `git diff <SHA_A> <SHA_B>` doesn't handle the "first commit
in the repo has no parent" case cleanly, which is why file lists — not SHA
ranges — are used for the two commit-shaped events.

For every event:

1. Resolve the current branch (`git rev-parse --abbrev-ref HEAD`).
2. Acquire a per-`(repo, branch)` lock:
   `$CLAUDE_ENV_HOME/state/locks/<repo>__<branch>.lock`,
   `fcntl.flock(fd, LOCK_EX | LOCK_NB)`. If the lock is already held (another
   re-index is in flight — e.g. an interactive rebase replaying several
   commits, each firing `post-commit`), log one line
   (`skipped: reindex already running for <repo>/<branch>`) and exit 0
   immediately, **before** touching the embedder. This is the fix for a real
   cost problem: loading the GGUF embedding model is not free, and a rebase or
   a rapid series of checkouts can fire these hooks many times in a few
   seconds.
3. Dispatch by event:
   - **`commit`** — the file list is already given (computed by the hook
     script exactly as today: `git diff-tree --no-commit-id --name-only -r
     HEAD`), so this is `Indexer(repo_root).incremental(changed)` directly.
     (Behavior-identical to today's `post-commit` → `incremental_index.py`;
     moved here so all three events share one lock/dispatch path.)
   - **`merge`** — same as `commit`, given the file list computed by the hook
     script as `git diff --name-only ORIG_HEAD HEAD` (git sets `ORIG_HEAD`
     before any merge, including a `git pull`'s fast-forward — and it always
     exists by the time `post-merge` fires, so there's no first-commit-style
     edge case here). Covers remote changes landing via pull/merge/rebase.
   - **`checkout`** — ignored entirely unless `is_branch_flag == "1"` (a real
     branch switch; git also fires this hook for plain file-level checkouts,
     which we don't care about). Then:
     - Look up `rag_index_state` for `(repo, current_branch)`.
     - **No row** (branch never indexed) → `Indexer(repo_root).full_index()`
       (a full walk of the new branch's tracked files — equivalent to what
       `branch_index.py` does, but no need to checkout/restore since we're
       already on the branch post-checkout).
     - **Row exists** → `git diff --name-only <last_commit> <new_sha>`,
       `Indexer(repo_root).incremental(changed)` (catches up commits made on
       that branch elsewhere since it was last indexed here).
4. Release the lock (context manager; released on any exit path, including
   exceptions — errors are logged, never raised to the caller, matching the
   existing "never block the git operation" posture).

`git_sync.py` calls `Indexer.incremental()` directly (the same method
`rag/incremental_index.py` already wraps) rather than shelling out to that
script, so the lock stays held for the whole operation from one process —
shelling out to a second script would need its own coordination for the same
lock, which is unnecessary complexity for no benefit. `incremental_index.py`
itself is untouched and keeps working standalone for manual/ad-hoc use
(`claude-env reindex`).

### 2. Hook scripts — `scripts/post-commit` (updated), `scripts/post-merge`,
`scripts/post-checkout` (new)

All three keep today's exact shape: resolve `$CLAUDE_ENV_HOME/venv/bin/python`
(exit 0 non-fatally if missing, matching today's behavior), pass git's own
hook arguments straight through, run in the background
(`nohup ... >> logs/incremental_index.log 2>&1 &`), `exit 0` always so the
git operation itself is never blocked or slowed down.

```bash
# scripts/post-commit (updated body, same install path, same CHANGED computation
# as today — only the dispatch target changes, from incremental_index.py to git_sync.py)
CHANGED=$(git diff-tree --no-commit-id --name-only -r HEAD || true)
[[ -z "$CHANGED" ]] && exit 0
nohup "$VENV_PY" "${CLAUDE_ENV_HOME}/rag/git_sync.py" "$REPO_ROOT" commit $CHANGED \
  >> "${CLAUDE_ENV_HOME}/logs/incremental_index.log" 2>&1 &

# scripts/post-merge (new) — git passes one arg (squash flag, unused);
# ORIG_HEAD is set by git before any merge, including a pull's fast-forward
CHANGED=$(git -C "$REPO_ROOT" diff --name-only ORIG_HEAD HEAD || true)
[[ -z "$CHANGED" ]] && exit 0
nohup "$VENV_PY" "${CLAUDE_ENV_HOME}/rag/git_sync.py" "$REPO_ROOT" merge $CHANGED \
  >> "${CLAUDE_ENV_HOME}/logs/incremental_index.log" 2>&1 &

# scripts/post-checkout (new) — git passes: <prev_sha> <new_sha> <is_branch_flag>
nohup "$VENV_PY" "${CLAUDE_ENV_HOME}/rag/git_sync.py" "$REPO_ROOT" checkout "$1" "$2" "$3" \
  >> "${CLAUDE_ENV_HOME}/logs/incremental_index.log" 2>&1 &
```

### 3. `scripts/register_repo.py` — install/upgrade all three hooks

`_install_post_commit()` becomes `_install_git_hooks()`, looping over
`("post-commit", "post-merge", "post-checkout")` with the same idempotent,
marker-based, never-clobber-a-foreign-hook logic already in place (a
`claude-env` marker comment in the script; skip with a message if a
non-claude-env hook of the same name already exists; skip as "already
installed" if content is byte-identical).

The existing `--no-post-commit` flag is kept (it is a documented, public CLI
flag — `docs/guide/onboarding.md`) but now gates all three hooks as one unit,
since they are one feature (`claude-env register --help` / the onboarding
guide's description of the flag is updated to say so, not renamed).

Re-running `claude-env onboard`/`register` on an already-onboarded repo is
already safe (idempotent) — this is the upgrade path for existing repos like
`nexus-sdv` to pick up `post-merge`/`post-checkout` without disturbing
anything else onboarding manages.

### Data flow summary

```
git event (commit/merge/checkout)
  -> hook script (nohup, background, never blocks git)
  -> rag/git_sync.py: acquire per-(repo,branch) lock (skip if held)
  -> resolve changed-file list (diff or full walk)
  -> Indexer.incremental() / Indexer.full_index()   [unchanged, existing code]
  -> LanceDB upsert + rag_file_state + rag_index_state   [unchanged, existing code]
```

### Error handling

Identical posture to today's `post-commit`: nothing here can block or fail a
git operation. Missing venv → log + exit 0. Any exception inside
`git_sync.py`'s dispatch (e.g. a transient LanceDB error) is caught, logged
to `logs/incremental_index.log`, and exits 0. Lock contention is not an
error — it's the expected, correct outcome of two triggers landing close
together.

### Testing

`tests/test_git_sync.py`, modeled on the existing
`tests/test_incremental_index.py` pattern (temp git repo + temp DB + a fake
embedder/store so no model download is needed):

- `commit` event re-indexes exactly the given file list.
- `merge` event re-indexes exactly the given file list.
- `checkout` event with `is_branch_flag=0` (file checkout) is a no-op.
- `checkout` onto a branch with no `rag_index_state` row triggers a full
  index of that branch.
- `checkout` onto a previously-indexed branch re-indexes only the commits
  made since `last_commit`.
- Lock contention: holding the lock file externally causes a second
  `git_sync.py` invocation for the same `(repo, branch)` to skip immediately
  (asserted via a fake `Indexer` that would raise if constructed, proving the
  embedder was never touched).
- `scripts/register_repo.py`'s hook installer installs all three hook files,
  is idempotent on a second run, and never overwrites a foreign (non-claude-env)
  hook of the same name — extending the existing `tests/test_onboard_helpers.py`
  coverage for `_install_post_commit` (renamed `_install_git_hooks`;
  `test_post_commit_install_and_idempotency` /
  `test_post_commit_does_not_clobber_foreign_hook` /
  `test_post_commit_skips_non_git` become table-driven over the three hook
  names instead of hardcoding `post-commit`).

## Out of scope

- Uncommitted (unsaved-to-git) working-tree changes.
- Retention/cleanup of a branch's LanceDB table after the branch is deleted.
- Any polling/background daemon or SessionStart-based staleness check — event-driven
  git hooks are the sole mechanism.
- Bare clones / worktrees / submodules where `.git` is a file, not a
  directory — already out of scope for hook installation today
  (`_install_post_commit` already skips these; `_install_git_hooks` keeps the
  same skip).
