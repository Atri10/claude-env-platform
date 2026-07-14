---
name: git-workflow
description: >-
  Git commit discipline, branch strategy, PR hygiene, meaningful commit
  messages, and when to squash vs preserve history. Use when committing
  changes, creating a PR, reviewing git history, or deciding how to structure
  a branch. Keeps the project history readable, bisectable, and auditable.
---

# Git Workflow

Git history is **project documentation that never goes stale**. A commit is not
a save point — it is a record of *why* a change was made, authored by the
person with the most context. Invest in it now; it pays dividends in every
future debugging session and code review.

This is a claude-env-governed repository. All git operations that mutate state
(commit, push, rebase, merge, reset) require `terminal.run` approval. Use the
`git.*` MCP tools for read operations (log, diff, blame, status).

---

## Commit discipline

### One logical change per commit
A commit should contain **one cohesive change**: one feature, one bug fix, one
refactoring, one dependency update. A commit that does three things is three
commits.

**Why:** `git bisect` can only identify the commit that introduced a regression.
A commit that mixes a bug fix with a refactoring and a dependency update makes
it impossible to know which part caused the regression — and impossible to
revert just the problematic part.

### Write the commit message to explain *why*, not *what*
The diff already shows what changed. The commit message explains why.

**Bad:** `fix bug` / `update code` / `WIP` / `changes`

**Bad (describes what, not why):**
> Add null check before calling user.email

**Good (explains why):**
> Guard against nil user in checkout when session expires mid-flow
>
> A user whose session expires between cart creation and checkout can reach
> the payment step with a nil user object. The nil check prevents a panic
> and redirects them to the login page with the cart preserved in session.

### Commit message format

```
<type>(<scope>): <imperative summary, max 72 chars>

<body: the why — what problem this solves, what was wrong before,
 what constraints drove the decision. Wrap at 72 chars.
 Leave blank if the summary is fully self-explanatory.>

<footer: breaking changes, issue references, co-authors>
```

**Types:** `feat` · `fix` · `refactor` · `test` · `docs` · `chore` · `perf` · `ci`

**Imperative mood in the summary.** "Add rate limiting" not "Added rate
limiting" or "Adds rate limiting." Git itself uses imperative mood ("Merge
branch…", "Revert…").

**Summary line under 72 characters.** It appears in `git log --oneline`,
GitHub PR lists, and terminal outputs. Truncation hides information.

### Commit often on green

Commit after every small, working step — not after hours of work. Small commits:
- Give you fine-grained rollback points.
- Make `git bisect` effective.
- Produce a readable history of how the solution was built.
- Make code review easier (reviewers can read the story).

**Never commit broken code to a shared branch.** If you need a checkpoint
mid-work, use a local branch or `git stash`.

### Atomic commits — the test

A commit is atomic if:
1. The test suite is green at that commit (checkout it, run tests — green).
2. The change is fully self-contained — it does not depend on a future commit
   to compile or make sense.
3. It can be reverted independently without breaking other commits.

---

## Branch strategy

### Branch naming
`<type>/<short-description>` — lowercase, hyphen-separated.

- `feat/user-rate-limiting`
- `fix/checkout-nil-user`
- `refactor/extract-order-service`
- `chore/update-pyyaml`
- `docs/api-auth-guide`

### Never commit directly to main/master
All changes go through a branch and a PR. Even one-line fixes. This ensures:
- A second pair of eyes on every change.
- The CI suite runs before merge.
- The history of main is a sequence of intentional merges, not ad-hoc edits.

### Branch lifetime
Keep branches short-lived. A branch that lives longer than a few days
accumulates merge conflicts and drift. If a feature is large, break it into
vertical slices and merge them incrementally behind a feature flag.

### Keeping a branch up to date
Prefer `git rebase origin/main` over `git merge origin/main` on feature
branches. Rebase keeps the history linear and makes the final PR diff clean.
Merge commits in a feature branch add noise with no information.

**Exception:** if the branch is shared with others, prefer merge to avoid
rewriting shared history.

---

## Pull requests

### PR size — the most important PR discipline
A PR that touches 10 files and 200 lines gets reviewed carefully.
A PR that touches 40 files and 800 lines gets a rubber stamp.

**Target:** < 400 lines changed per PR. If a feature is larger:
- Extract preparatory refactoring as a separate PR (reviewed and merged first).
- Use vertical slices: each PR adds an end-to-end thin slice of the feature.
- Use feature flags to merge incomplete features safely.

### PR description — the second most important thing
The PR description is the first thing a reviewer reads. It should answer:
1. **What** does this change?
2. **Why** is this change needed? (Link to the issue or requirement.)
3. **How** was the approach chosen? (If there were alternatives, why this one?)
4. **How to test** it? (What to look for, what scenario to exercise.)
5. **Any risks or known limitations?**

A one-line PR description for a 200-line change is a code review tax on the
reviewer.

### PR checklist before requesting review
- [ ] Tests added for every new behaviour.
- [ ] `terminal.run_tests` run; output is green.
- [ ] Self-reviewed with the `code-review` skill.
- [ ] No unrelated changes mixed in.
- [ ] PR description answers the five questions above.
- [ ] No secrets, credentials, or PII in the diff.
- [ ] Breaking changes documented (if any).

### Responding to review comments
- Acknowledge every comment, even if you disagree.
- Distinguish: "fixed in X commit" vs. "I'll defer this to a follow-up issue"
  vs. "I disagree because…".
- Don't push fixup commits and force-push on a shared review branch — add new
  commits (`fixup! <original message>`) and squash at merge.

---

## Squash vs merge vs rebase

| Strategy | When to use | Effect on history |
|----------|------------|-------------------|
| **Squash merge** | Feature branch with messy WIP commits; history of main matters more than individual steps | Single clean commit on main; branch history discarded |
| **Merge commit** | Preserving the branch history is valuable; multi-person collaboration | Merge commit + all branch commits on main |
| **Rebase** | Linear history; small, clean feature branch; all commits are meaningful | Branch commits grafted onto main, no merge commit |

**Default recommendation for this repo:** squash merge small feature branches
with messy WIP commits; rebase small clean branches; use merge commits for
long-running collaboration branches where the history of individual steps is
valuable.

**Never force-push to main or a shared branch** unless explicitly coordinating
with the team. Force-pushing rewrites shared history and causes divergence for
everyone who has pulled.

---

## Reading and using git history

### `git log` — understand what happened
```bash
git log --oneline --graph --decorate  # compact visual
git log -p --follow -- path/to/file   # history of one file with diffs
git log --author="Name" --since="1 week ago"
```

### `git blame` — understand why a line exists
```bash
git blame -L 40,60 src/service.py     # lines 40-60 with author and commit
```
Use `git.blame` MCP tool to stay within governance.

### `git bisect` — find when a regression was introduced
The fastest path from "it used to work" to "this is the exact commit that broke
it." See the `debugging` skill for the full procedure.

### `git diff` — review before committing
```bash
git diff --staged   # what is about to be committed
git diff HEAD~1     # what the last commit changed
```
Review every staged diff before committing. Accidental debug prints, leftover
TODOs, and partial changes are caught here, not in review.

---

## What not to commit

| What | Why |
|------|-----|
| Secrets, API keys, credentials | Permanently in history even after deletion; rotate immediately if committed |
| Build artifacts, compiled output | Bloats the repo; regenerated on demand |
| IDE-specific files | Personal preference; belongs in `.gitignore` |
| Large binary files | Git is not optimised for binaries; use LFS or external storage |
| Temporary debug code (`print`, `console.log`, breakpoints) | Will confuse the next person; review `git diff --staged` before every commit |
| Commented-out code | Delete it; git history preserves it if needed |
| Merge conflict markers (`<<<<`, `>>>>`) | Always check before committing a resolved conflict |

---

## Governance notes

This repo is governed by claude-env. All git operations that change state
require `terminal.run` approval:
- `git commit`, `git push`, `git merge`, `git rebase`, `git reset`, `git tag`
- These are state-mutating — they go through the approval gate, which records
  the OS `user@host` in the audit ledger.

Read operations (`git log`, `git diff`, `git blame`, `git status`, `git show`)
are available directly via the `git.*` MCP tools without approval.

**Never `--amend` a commit that has been pushed** to a shared branch. This
rewrites shared history and will force teammates to reset. If you need to fix
a recent commit on a local branch, `--amend` is fine. On a shared branch, add
a new commit.
