#!/usr/bin/env python3
"""
terminal MCP server :: constrained, allow-listed command execution.

There is no general shell here. Only three classes of read-only / non-mutating
commands run automatically, each mapped to a vetted argv template:

  terminal.run_tests       -> the repo's configured test command
  terminal.run_benchmarks  -> the repo's configured benchmark command
  terminal.run_audit       -> the repo's configured security-audit command

Commands run with:
  * a hard timeout
  * the repo root as cwd (no escaping, including via `cd`)
  * a scrubbed environment (no inherited secrets)
  * shell=False, argv lists only -- NO bash/sh process is ever spawned

Within that no-shell sandbox, `;`, `&&`, `||`, `|`, and a leading `cd dir &&` are
still supported: the command string is tokenized (shlex, punctuation-char mode)
and each stage is run as its own argv via subprocess, piped/short-circuited in
Python. Anything that genuinely needs a real shell -- redirection (`>`, `>>`,
`<`), subshells (`(...)`), backgrounding (`&`), command substitution (`` ` ``,
`$(...)`) -- is rejected with a clear error rather than silently doing nothing
or being handed to a shell interpreter. See `_tokenize`/`_split_pipelines`/`_run`.

Anything state-mutating maps to `terminal.run`, which opens a human approval,
surfaces the approvals web UI, and BLOCKS until the operator approves or denies.
On approval the command runs under the same no-shell sandbox described above
(scrubbed env, repo-root cwd, timeout); on denial/timeout it does not.
There is no `terminal.exec_unrestricted` tool at all.

Configuration: each command must be set per-repo in `${repo}/.claude/commands.json`.
An unconfigured command is NOT run — it returns a directive telling the operator to
configure it (so we never run a toolchain-wrong default like pytest on a Go repo).
`claude-env onboard` auto-writes commands.json for single-toolchain repos. stdio
server. Requires: pip install mcp
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

_HOME = Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))
for p in (_HOME, _HOME / "audit", _HOME / "lib"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from audit.audit_logger import AuditLogger  # noqa: E402
from lib.logging_setup import get_logger  # noqa: E402

# stderr-safe (never stdout — stdio protocol channel); also lands in
# logs/mcp-terminal.log.
_log = get_logger("mcp-terminal", stderr=True)

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool
except ImportError:
    sys.stderr.write("terminal: the 'mcp' package is required (pip install mcp)\n")
    raise

REPO_ROOT = Path(os.environ.get("CLAUDE_ENV_REPO_ROOT", os.getcwd())).resolve()
# Per-repo disposable scratch directory -- same convention
# mcp-servers/filesystem-policy/server.py uses for its scratch:// scheme
# (computed independently here since this is a separate process; both agree
# without IPC because CLAUDE_ENV_REPO_ROOT/CLAUDE_ENV_HOME are resolved
# identically for every MCP server). terminal.run can start here instead of
# REPO_ROOT via the optional in_scratch argument -- still requires the same
# human approval as any other terminal.run command.
SCRATCH_ROOT = (_HOME / "scratch" / REPO_ROOT.name).resolve()
SESSION_ID = os.environ.get("CLAUDE_ENV_SESSION", "mcp-terminal")
TIMEOUT_S = int(os.environ.get("CLAUDE_ENV_CMD_TIMEOUT", "600"))
# Approval flow: how long terminal.run blocks waiting for a human decision, how
# often it polls, and the approvals-UI port to auto-open.
_WAIT_S = int(os.environ.get("CLAUDE_ENV_APPROVAL_WAIT_S", "120"))
_POLL_S = 2
# CLAUDE_ENV_APPROVAL_PORT (if set) is a *preferred* port hint passed to the
# approvals UI; the actual port is discovered from the service registry.

# Suggested commands, surfaced in the "not configured" hint. NOT auto-run —
# a command only executes when set explicitly in .claude/commands.json.
_DEFAULTS = {
    "run_tests": "pytest -q",
    "run_benchmarks": "pytest -q --benchmark-only",
    "run_audit": "pip-audit",
}

# Environment variables that may pass through; everything else is stripped.
_ENV_ALLOW = {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "VIRTUAL_ENV", "PWD"}

_audit = AuditLogger(session_id=SESSION_ID, actor="terminal-mcp",
                     repo=REPO_ROOT.name)
server = Server("terminal")


def _load_commands() -> tuple[dict, set]:
    """Return (commands, explicitly_configured_keys). A key is 'configured' only
    if <repo>/.claude/commands.json sets it — so we never silently run a default
    that's wrong for the repo's toolchain (e.g. pytest on a Go repo)."""
    cfg = REPO_ROOT / ".claude" / "commands.json"
    cmds = dict(_DEFAULTS)
    configured: set = set()
    if cfg.exists():
        try:
            user = json.loads(cfg.read_text())
            for k in _DEFAULTS:
                if isinstance(user.get(k), str) and user[k].strip():
                    cmds[k] = user[k]
                    configured.add(k)
        except Exception:
            # malformed commands.json — fall back to built-in defaults rather
            # than failing; log so a broken override file is diagnosable.
            _log.warning("could not parse %s; using default commands", cfg,
                         exc_info=True)
    return cmds, configured


def _scrubbed_env() -> dict:
    return {k: v for k, v in os.environ.items() if k in _ENV_ALLOW}


def _repo_tier() -> int | None:
    """Best-effort read of this repo's privacy tier for the approval record."""
    import re
    pol = REPO_ROOT / ".claude" / "repo-policy.yaml"
    try:
        for line in pol.read_text().splitlines():
            m = re.match(r"\s*tier\s*:\s*([0-3])\b", line)
            if m:
                return int(m.group(1))
    except Exception:
        # tier is advisory metadata on the approval record; absence is fine.
        _log.debug("could not read repo tier from %s", pol, exc_info=True)
    return None


def _ensure_approvals_ui() -> None:
    """Make sure the approvals server is running, and open ONE browser tab the
    first time it is started — never per request. The server is a long-lived,
    single-tab queue: once it's up, every new approval just appears in that same
    page (which auto-refreshes), so we must not `open` the URL again for each
    command or tabs pile up. Reusing an already-running server therefore opens
    nothing. Disable browser opening entirely with CLAUDE_ENV_APPROVAL_AUTO_UI=false
    (the server still starts and the request still blocks)."""
    import time
    try:
        from lib.services import get as _svc_get
        svc = _svc_get("approvals")
        if svc is not None:
            # Already running — the operator already has (or can reopen) the tab.
            # Opening again is exactly what caused tabs to pile up, so don't.
            return
        # Not running -> start it once, and open the browser once, now.
        ui = _HOME / "agents" / "orchestration" / "approvals_ui.py"
        cmd = [sys.executable, str(ui)]
        pref = os.environ.get("CLAUDE_ENV_APPROVAL_PORT")
        if pref:                                      # optional preferred-port hint
            cmd += ["--port", pref]
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        for _ in range(30):                           # wait for it to register (~3s)
            svc = _svc_get("approvals")
            if svc:
                break
            time.sleep(0.1)
        if not svc:
            return
        if os.environ.get("CLAUDE_ENV_APPROVAL_AUTO_UI", "true").lower() != "true":
            return                                    # started, but don't open a tab
        url = svc["url"]
        if sys.platform == "darwin":
            subprocess.run(["open", url], capture_output=True, timeout=5)
        elif sys.platform.startswith("linux"):
            subprocess.run(["xdg-open", url], capture_output=True, timeout=5)
    except Exception:
        # auto-opening the approval UI is a convenience; the request still blocks
        # and the operator can open the URL manually. Log the failure.
        _log.info("could not ensure/auto-open approval UI in a browser", exc_info=True)


async def _await_decision(req_id: str) -> tuple[str, str | None]:
    """Block until the operator approves/denies this request (or we time out).
    Polls the human_approvals row the approvals UI updates in another process."""
    import asyncio
    from lib.db import get_db
    db = get_db()
    waited = 0
    while waited < _WAIT_S:
        row = db.query_one(
            "SELECT decision, decided_by FROM human_approvals WHERE request_id=?",
            (req_id,))
        if row and row["decision"] in ("approved", "denied"):
            return row["decision"], row["decided_by"]
        await asyncio.sleep(_POLL_S)
        waited += _POLL_S
    return "pending", None


# Chaining/piping connectors we support WITHOUT a real shell (see _split_pipelines).
_ALLOWED_OPS = {";", "&&", "||", "|"}
# shlex's punctuation_chars mode only ever emits maximal runs of ();<>|&, so
# every punctuation-only token not in _ALLOWED_OPS is shell-ish (redirection,
# subshells, backgrounding, command substitution, or a malformed run like
# ';;') and is rejected outright -- this is the line the argv-only guarantee
# in the module docstring depends on.
_PUNCTUATION_CHARS = set("();<>|&")


class _CommandError(Exception):
    """A command string used a shell feature this sandbox intentionally does
    not support (redirection, subshells, backgrounding, substitution)."""


def _tokenize(command: str) -> list[str]:
    """Split a command string into words + operators, without a shell.

    Uses shlex's punctuation-char mode so ';', '&&', '||', '|' come out as
    their own tokens while quoted occurrences (e.g. "a && b") stay fused into
    a single literal word -- this is what lets us recognize *unquoted*
    chaining/piping without invoking bash."""
    lex = shlex.shlex(command, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        tokens = list(lex)
    except ValueError as e:
        raise _CommandError(f"could not parse command: {e}")
    for t in tokens:
        if t in _PUNCTUATION_CHARS or set(t) <= _PUNCTUATION_CHARS:
            if t not in _ALLOWED_OPS:
                raise _CommandError(
                    f"unsupported shell operator '{t}' -- redirection, backgrounding, "
                    f"subshells, command substitution, and stray/doubled operators are "
                    f"not supported by this argv-only sandbox (no real shell is "
                    f"invoked). Supported: chaining with ';', '&&', '||', and piping "
                    f"with '|'.")
    return tokens


def _split_pipelines(tokens: list[str]) -> list[dict]:
    """[[tok,...]] -> [{"connector": ";"|"&&"|"||"|None, "commands": [[argv],...]}]

    Each returned pipeline is one or more argv lists joined by '|'; pipelines
    are joined to each other by the connector that precedes them (None for
    the first). '&&'/'||' short-circuit on the previous pipeline's exit code;
    ';' always runs regardless."""
    pipelines: list[dict] = []
    cur_cmd: list[str] = []
    cur_pipeline_cmds: list[list[str]] = []
    pending_connector: str | None = None

    def flush_cmd():
        if not cur_cmd:
            raise _CommandError(
                "empty command segment -- check for a leading/trailing/doubled "
                "';', '&&', '||' or '|'")
        cur_pipeline_cmds.append(list(cur_cmd))
        cur_cmd.clear()

    def flush_pipeline(connector):
        flush_cmd()
        pipelines.append({"connector": connector, "commands": list(cur_pipeline_cmds)})
        cur_pipeline_cmds.clear()

    for t in tokens:
        if t == "|":
            flush_cmd()
        elif t in (";", "&&", "||"):
            flush_pipeline(pending_connector)
            pending_connector = t
        else:
            cur_cmd.append(t)
    if cur_cmd or cur_pipeline_cmds:
        flush_pipeline(pending_connector)
    if not pipelines:
        raise _CommandError("empty command")
    return pipelines


def _run_pipeline(argvs: list[list[str]], cwd: str) -> tuple[int, str, str]:
    """Run one or more argv lists connected by '|', piping stdout->stdin
    between them via Python (never a shell). Returns (exit_code, stdout, stderr)
    of the LAST stage, matching bash pipeline exit-status semantics."""
    procs: list[subprocess.Popen] = []
    try:
        prev_stdout = None
        for i, argv in enumerate(argvs):
            is_last = i == len(argvs) - 1
            p = subprocess.Popen(
                argv, cwd=cwd, env=_scrubbed_env(), stdin=prev_stdout,
                stdout=subprocess.PIPE if not is_last else subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, shell=False)
            if prev_stdout is not None:
                prev_stdout.close()
            prev_stdout = p.stdout
            procs.append(p)
        out, err = procs[-1].communicate(timeout=TIMEOUT_S)
        for p in procs[:-1]:
            p.wait(timeout=TIMEOUT_S)
        return procs[-1].returncode, out or "", err or ""
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()


def _run(template: str, kind: str, root: Path | None = None) -> str:
    """Execute a command string with NO real shell involved.

    Supports the chaining/piping an agent naturally writes (';', '&&', '||',
    '|', including a leading 'cd dir && ...') by tokenizing with shlex and
    running each stage as its own argv via subprocess, never bash -c. Anything
    that needs a real shell -- redirection, subshells, backgrounding, command
    substitution -- is rejected with a clear error instead of silently doing
    nothing or (worse) being handed to a shell interpreter.

    root defaults to REPO_ROOT (terminal.run_tests/run_benchmarks/run_audit,
    and terminal.run's normal case). Pass SCRATCH_ROOT to start the command
    inside the per-repo scratch directory instead -- 'cd' escapes are then
    checked against THAT root, not REPO_ROOT, so a scratch-rooted command
    can move around inside scratch but still can't 'cd' out of it (or into
    the real repo)."""
    if root is None:
        root = REPO_ROOT
    try:
        pipelines = _split_pipelines(_tokenize(template))
    except _CommandError as e:
        return f"ERROR: {e}"

    cwd = str(root)
    last_exit = 0
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    ran_any = False

    for pipeline in pipelines:
        connector = pipeline["connector"]
        if connector == "&&" and last_exit != 0:
            continue
        if connector == "||" and last_exit == 0 and ran_any:
            continue

        argvs = pipeline["commands"]
        # 'cd' is a shell builtin, not an executable -- handle it as a cwd
        # change for the rest of the chain instead of trying to exec it.
        if len(argvs) == 1 and argvs[0][0] == "cd":
            target = argvs[0][1] if len(argvs[0]) > 1 else str(root)
            new_cwd = (Path(cwd) / target).resolve()
            try:
                if new_cwd != root and root not in new_cwd.parents:
                    raise _CommandError(f"cd target '{target}' escapes the allowed root")
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

        ran_any = True
        try:
            last_exit, out, err = _run_pipeline(argvs, cwd)
        except subprocess.TimeoutExpired:
            _audit.tool_call(tool=f"terminal.{kind}", args={"cmd": template},
                             result_kind="timeout")
            return f"TIMEOUT after {TIMEOUT_S}s: {template}"
        except FileNotFoundError as e:
            missing = argvs[0][0] if len(argvs) == 1 else str(e)
            return f"ERROR: command not found: {missing}"
        stdout_parts.append(out)
        if err:
            stderr_parts.append(err)

    _audit.tool_call(tool=f"terminal.{kind}", args={"cmd": template},
                     result_kind=f"exit{last_exit}")
    stdout_tail = "".join(stdout_parts)[-6000:]
    stderr_tail = "\n".join(stderr_parts)[-2000:]
    tail = stdout_tail + (f"\n[stderr]\n{stderr_tail}" if stderr_tail else "")
    return f"exit={last_exit}\n{tail}"

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(name="terminal.run_tests",
             description="Run this repo's configured test command (from "
                         "<repo>/.claude/commands.json) in a sandbox: repo-root cwd, "
                         "scrubbed env (no inherited secrets), no real shell is spawned "
                         "(';'/'&&'/'||'/'|'/'cd' are supported without one; redirection/"
                         "subshells/backgrounding/substitution are rejected), hard timeout. "
                         "Takes no arguments — you cannot choose the command, only trigger the "
                         "vetted one. If no test command is configured it runs NOTHING and "
                         "returns a 'NOT CONFIGURED' directive. Returns exit code + truncated "
                         "stdout/stderr; audited.",
             inputSchema={"type": "object", "properties": {}}),
        Tool(name="terminal.run_benchmarks",
             description="Run this repo's configured benchmark command from "
                         "<repo>/.claude/commands.json, in the same no-shell sandbox as "
                         "run_tests (repo-root cwd, scrubbed env, timeout; ';'/'&&'/'||'/'|'/"
                         "'cd' supported, redirection/subshells/substitution rejected). Takes "
                         "no arguments. Returns 'NOT CONFIGURED' and runs nothing if unset; "
                         "otherwise returns exit code + truncated output. Audited.",
             inputSchema={"type": "object", "properties": {}}),
        Tool(name="terminal.run_audit",
             description="Run this repo's configured security-audit command (e.g. pip-audit) "
                         "from <repo>/.claude/commands.json, in the same no-shell sandbox as "
                         "run_tests (repo-root cwd, scrubbed env, timeout; ';'/'&&'/'||'/'|'/"
                         "'cd' supported, redirection/subshells/substitution rejected). Takes "
                         "no arguments. Returns 'NOT CONFIGURED' and runs nothing if unset; "
                         "otherwise returns exit code + truncated output. Audited.",
             inputSchema={"type": "object", "properties": {}}),
        Tool(name="terminal.run",
             description="Request execution of an arbitrary (typically state-mutating) command "
                         "that isn't one of the vetted run_tests/benchmarks/audit templates. "
                         "This opens a human-approval request, surfaces the approvals web UI, "
                         "and BLOCKS until an operator approves or denies (or it times out, "
                         "~120s). On approval it runs in the SAME no-shell sandbox — cwd, "
                         "scrubbed env, timeout, no bash/sh process ever spawned; "
                         "';'/'&&'/'||'/'|'/'cd' chaining is supported without a real shell, "
                         "while redirection/subshells/backgrounding/command substitution are "
                         "rejected outright — and it returns who approved plus exit code and "
                         "output; on denial/timeout nothing runs. Use only when a human is "
                         "available to approve; the whole request is audited.\n\n"
                         "By default the command starts in the repo root. Set in_scratch=true "
                         "to start it in this repo's disposable scratch directory instead "
                         "($CLAUDE_ENV_HOME/scratch/<repo>/ — the same directory "
                         "filesystem.read/write/list reach via 'scratch://' paths). Use this "
                         "for throwaway work: downloading/building intermediate artifacts, "
                         "installing packages to inspect them, running a one-off script "
                         "against files you've already written to scratch:// — anything you "
                         "don't want touching the real repo tree. 'cd' within the scratch "
                         "directory is allowed; 'cd' out of it (into the repo root or "
                         "anywhere else) is rejected, same as the repo-root case. This still "
                         "requires human approval like any other terminal.run call — there is "
                         "no unattended/unapproved execution path for scratch or anywhere "
                         "else.",
             inputSchema={"type": "object",
                          "properties": {"command": {"type": "string",
                                          "description": "The command line to request, e.g. "
                                          "'npm install' or 'grep foo file.txt | wc -l'. "
                                          "Parsed with shlex into argv stages joined by "
                                          "';'/'&&'/'||'/'|' -- no real shell is ever invoked, so "
                                          "redirection ('>', '<'), subshells, backgrounding "
                                          "('&'), command substitution ('`'/'$()'), globs, and "
                                          "env-var expansion are NOT interpreted and are "
                                          "rejected with an error."},
                                         "in_scratch": {"type": "boolean",
                                          "description": "If true, the command starts in this "
                                          "repo's scratch directory instead of the repo root. "
                                          "Defaults to false (repo root)."}},
                          "required": ["command"]}),
    ]


def _run_configured(cmds: dict, configured: set, kind: str) -> str:
    """Run a command only if the repo explicitly configured it; otherwise return
    a clear directive (never blindly run the toolchain-specific default)."""
    if kind not in configured:
        return (f"NOT CONFIGURED: no '{kind}' command is set for this repo. Add it to "
                f"{REPO_ROOT}/.claude/commands.json — e.g. "
                f'{{"run_tests": "go test ./..."}} (Go), "npm test" (JS), '
                f'"pytest -q" (Python), "cargo test" (Rust), or "make test". '
                f"(No command was run.)")
    return _run(cmds[kind], kind)


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    cmds, configured = _load_commands()
    if name == "terminal.run_tests":
        return [TextContent(type="text", text=_run_configured(cmds, configured, "run_tests"))]
    if name == "terminal.run_benchmarks":
        return [TextContent(type="text",
                            text=_run_configured(cmds, configured, "run_benchmarks"))]
    if name == "terminal.run_audit":
        return [TextContent(type="text", text=_run_configured(cmds, configured, "run_audit"))]
    if name == "terminal.run":
        command = str(arguments.get("command", "")).strip()
        in_scratch = bool(arguments.get("in_scratch", False))
        if not command:
            return [TextContent(type="text", text="ERROR: empty command")]
        root = SCRATCH_ROOT if in_scratch else REPO_ROOT
        _audit.security_event(category="terminal_gate", severity="low",
                              detail=f"state-mutating command requested "
                                     f"({'scratch' if in_scratch else 'repo root'}): {command}",
                              source="terminal.run")
        # Open a human approval, surface the real UI, then BLOCK until the operator
        # decides. On approval the command runs (same sandbox: argv-only, no shell,
        # scrubbed env, timeout, cwd = repo root or scratch per in_scratch); on
        # denial/timeout it does not.
        req_id = _audit.human_approval_request(
            agent="terminal",
            action=f"terminal.run{' (scratch)' if in_scratch else ''}: {command}",
            tier=_repo_tier())
        _ensure_approvals_ui()
        decision, by = await _await_decision(req_id)
        if decision == "approved":
            if in_scratch:
                SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
            out = _run(command, "run", root=root)
            return [TextContent(type="text",
                    text=f"APPROVED by {by} (request {req_id}). Executed:\n{out}")]
        if decision == "denied":
            return [TextContent(type="text",
                    text=f"DENIED by {by} (request {req_id}). Command was NOT run.")]
        return [TextContent(type="text",
                text=f"NO DECISION within {_WAIT_S}s — request {req_id} is still pending. "
                     f"Approve it in the approvals UI, then ask me to run it again.")]
    return [TextContent(type="text", text=f"ERROR: unknown or denied tool {name}")]


async def _serve() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(_serve())
