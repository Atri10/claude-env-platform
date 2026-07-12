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


def _run_in_dir(template: str, kind: str, cwd: str) -> str:
    """Thin wrapper so callers with a non-default cwd (only run_scratch today)
    read clearly at the call site without every _run() caller needing to pass
    base_cwd explicitly."""
    return _run(template, kind, base_cwd=cwd)


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
                         "~120s). On approval it runs in the SAME no-shell sandbox — repo-root "
                         "cwd, scrubbed env, timeout, no bash/sh process ever spawned; "
                         "';'/'&&'/'||'/'|'/'cd' chaining is supported without a real shell, "
                         "while redirection/subshells/backgrounding/command substitution are "
                         "rejected outright — and it returns who approved plus exit code and "
                         "output; on denial/timeout nothing runs. Use only when a human is "
                         "available to approve; the whole request is audited.",
             inputSchema={"type": "object",
                          "properties": {"command": {"type": "string",
                                          "description": "The command line to request, e.g. "
                                          "'npm install' or 'grep foo file.txt | wc -l'. "
                                          "Parsed with shlex into argv stages joined by "
                                          "';'/'&&'/'||'/'|' -- no real shell is ever invoked, so "
                                          "redirection ('>', '<'), subshells, backgrounding "
                                          "('&'), command substitution ('`'/'$()'), globs, and "
                                          "env-var expansion are NOT interpreted and are "
                                          "rejected with an error."}},
                          "required": ["command"]}),
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
        if not command:
            return [TextContent(type="text", text="ERROR: empty command")]
        _audit.security_event(category="terminal_gate", severity="low",
                              detail=f"state-mutating command requested: {command}",
                              source="terminal.run")
        # Open a human approval, surface the real UI, then BLOCK until the operator
        # decides. On approval the command runs (same sandbox: argv-only, no shell,
        # scrubbed env, repo-root cwd, timeout); on denial/timeout it does not.
        req_id = _audit.human_approval_request(
            agent="terminal", action=f"terminal.run: {command}", tier=_repo_tier())
        _ensure_approvals_ui()
        decision, by = await _await_decision(req_id)
        if decision == "approved":
            out = _run(command, "run")
            return [TextContent(type="text",
                    text=f"APPROVED by {by} (request {req_id}). Executed:\n{out}")]
        if decision == "denied":
            return [TextContent(type="text",
                    text=f"DENIED by {by} (request {req_id}). Command was NOT run.")]
        return [TextContent(type="text",
                text=f"NO DECISION within {_WAIT_S}s — request {req_id} is still pending. "
                     f"Approve it in the approvals UI, then ask me to run it again.")]
    if name == "terminal.run_scratch":
        command = str(arguments.get("command", "")).strip()
        if not command:
            return [TextContent(type="text", text="ERROR: empty command")]
        return [TextContent(type="text", text=_run_scratch(command))]
    return [TextContent(type="text", text=f"ERROR: unknown or denied tool {name}")]


async def _serve() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(_serve())
