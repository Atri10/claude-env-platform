#!/usr/bin/env python3
"""
claude-env :: Adapters - Terminal MCP Server
Constrained command execution: run_tests, run_benchmarks, run_audit (auto),
run (with human approval). All argv-only, no shell, scrubbed env.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from claudenv.adapters.config import get_config
from claudenv.domain.policy import PolicyEngine
from claudenv.domain.value_objects import RepoSlug, Tier, SessionId
from claudenv.ports import IAuditLogger


# --- Constants ---------------------------------------------------------------

_HOME = Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))
REPO_ROOT = Path(os.environ.get("CLAUDE_ENV_REPO_ROOT", os.getcwd())).resolve()
SCRATCH_ROOT = (_HOME / "scratch" / REPO_ROOT.name).resolve()
SESSION_ID = os.environ.get("CLAUDE_ENV_SESSION", "mcp-terminal")
TIMEOUT_S = int(os.environ.get("CLAUDE_ENV_CMD_TIMEOUT", "600"))
_WAIT_S = int(os.environ.get("CLAUDE_ENV_APPROVAL_WAIT_S", "120"))
_POLL_S = 2

_DEFAULTS = {
    "run_tests": "pytest -q",
    "run_benchmarks": "pytest -q --benchmark-only",
    "run_audit": "pip-audit",
}

_ENV_ALLOW = {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "VIRTUAL_ENV", "PWD"}

_ALLOWED_OPS = {";", "&&", "||", "|"}
_PUNCTUATION_CHARS = set("();<>|&")


class _CommandError(Exception):
    """Unsupported shell feature in command string."""
    pass


class TerminalServer:
    """Terminal command execution MCP server (argv-only, no shell)."""

    def __init__(
        self,
        repo_root: Path,
        audit_logger: IAuditLogger,
        policy_engine: PolicyEngine,
        session_id: str = "mcp-terminal",
    ):
        self.repo_root = repo_root
        self.audit = audit_logger
        self.engine = policy_engine
        self.session_id = session_id
        self.scratch_root = (_HOME / "scratch" / repo_root.name).resolve()

        self.server = Server("terminal")
        self._register_tools()

    # --- Tools ---------------------------------------------------------------

    def _register_tools(self) -> None:
        @self.server.list_tools()
        async def list_tools() -> list[Tool]:
            cmds, configured = self._load_commands()

            tools = []
            for key in ("run_tests", "run_benchmarks", "run_audit"):
                cmd = _DEFAULTS.get(key, "")
                if key in cmds:
                    cmd = cmds[key]
                desc = f"Run repo's configured {key.replace('_', ' ')} command (no shell, sandboxed)."
                if cmd:
                    desc += f" Current: {cmd}"
                else:
                    desc += " NOT CONFIGURED -- set in .claude/commands.json"
                tools.append(Tool(
                    name=f"terminal.{key}",
                    description=desc,
                    inputSchema={"type": "object", "properties": {}},
                ))

            tools.append(Tool(
                name="terminal.run",
                description=(
                    "Run arbitrary command with human approval. Opens approval UI, blocks until "
                    "decision. Runs in sandbox (no real shell, scrubbed env, repo-root cwd). "
                    "Supports chaining (; && || |) and leading 'cd dir &&'. "
                    "Use in_scratch=true for disposable scratch dir."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "Command line (argv-only, no shell). Supports ; && || | and leading cd dir &&"
                        },
                        "in_scratch": {
                            "type": "boolean",
                            "description": "Run in per-repo scratch dir instead of repo root",
                            "default": False
                        },
                    },
                    "required": ["command"],
                },
            ))
            return tools

        @self.server.call_tool()
        async def call_tool(name: str, arguments: dict) -> list[TextContent]:
            cmds, configured = self._load_commands()

            if name == "terminal.run_tests":
                return [TextContent(type="text", text=self._run_configured(cmds, configured, "run_tests"))]
            if name == "terminal.run_benchmarks":
                return [TextContent(type="text", text=self._run_configured(cmds, configured, "run_benchmarks"))]
            if name == "terminal.run_audit":
                return [TextContent(type="text", text=self._run_configured(cmds, configured, "run_audit"))]

            if name == "terminal.run":
                command = str(arguments.get("command", "")).strip()
                in_scratch = bool(arguments.get("in_scratch", False))
                if not command:
                    return [TextContent(type="text", text="ERROR: empty command")]

                root = self.scratch_root if in_scratch else self.repo_root

                self.audit.security_event(
                    category="terminal_gate", severity="low",
                    detail=f"state-mutating command requested "
                           f"({'scratch' if in_scratch else 'repo root'}): {command}",
                    source="terminal.run",
                )

                req_id = self.audit.human_approval_request(
                    agent="terminal",
                    action=f"terminal.run{' (scratch)' if in_scratch else ''}: {command}",
                    tier=self.engine.repo.tier if self.engine.repo else None,
                )
                self._notify_pending_approval()
                self._ensure_approvals_ui()

                decision, by = await self._await_decision(req_id)
                if decision == "approved":
                    if in_scratch:
                        self.scratch_root.mkdir(parents=True, exist_ok=True)
                    out = self._run(command, "run", root=root)
                    return [TextContent(type="text", text=f"APPROVED by {by} (request {req_id}). Executed:\n{out}")]
                if decision == "denied":
                    return [TextContent(type="text", text=f"DENIED by {by} (request {req_id}). Command was NOT run.")]
                return [TextContent(type="text", text=f"NO DECISION within {_WAIT_S}s -- request {req_id} is still pending. Approve it in the approvals UI, then ask me to run it again.")]

            return [TextContent(type="text", text=f"ERROR: unknown or denied tool {name}")]

    def _run_configured(self, cmds: dict, configured: set, kind: str) -> str:
        if kind not in configured:
            return (f"NOT CONFIGURED: no '{kind}' command is set for this repo. Add it to "
                    f"{self.repo_root}/.claude/commands.json -- e.g. "
                    f'{{"run_tests": "go test ./..."}} (Go), "npm test" (JS), '
                    f'"pytest -q" (Python), "cargo test" (Rust), or "make test". '
                    f"(No command was run.)")
        return self._run(cmds[kind], kind)

    # --- Commands loading ----------------------------------------------------

    def _load_commands(self) -> Tuple[dict, set]:
        cfg = self.repo_root / ".claude" / "commands.json"
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
                pass
        return cmds, configured

    # --- Approval flow -------------------------------------------------------

    async def _await_decision(self, req_id: str) -> Tuple[str, str | None]:
        import time
        from claudenv.adapters.config import get_config
        config = get_config()
        db = config.get_database()

        waited = 0
        while waited < _WAIT_S:
            row = db.query_one(
                "SELECT decision, decided_by FROM human_approvals WHERE request_id=?",
                (req_id,),
            )
            if row and row["decision"] in ("approved", "denied"):
                return row["decision"], row["decided_by"]
            await asyncio.sleep(_POLL_S)
            waited += _POLL_S
        return "pending", None

    def _notify_pending_approval(self) -> None:
        if os.environ.get("CLAUDE_ENV_APPROVAL_SOUND", "true").lower() == "false":
            return
        try:
            if sys.platform == "darwin":
                subprocess.run(["afplay", "/System/Library/Sounds/Ping.aiff"],
                              capture_output=True, timeout=5)
            elif sys.platform.startswith("linux"):
                subprocess.run(["canberra-gtk-play", "-i", "dialog-warning"],
                              capture_output=True, timeout=5)
        except Exception:
            pass  # best effort

    def _ensure_approvals_ui(self) -> None:
        # Approval UI is now handled by the approval service in the main process
        # This method is kept for compatibility but does nothing
        pass

    # --- Command parsing -----------------------------------------------------

    def _tokenize(self, command: str) -> list[str]:
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
                        f"subshells, command substitution not supported. Allowed: ; && || |"
                    )
        return tokens

    def _split_pipelines(self, tokens: list[str]) -> list[dict]:
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

    def _scrubbed_env(self) -> dict:
        return {k: v for k, v in os.environ.items() if k in _ENV_ALLOW}

    def _run_pipeline(self, argvs: list[list[str]], cwd: str) -> Tuple[int, str, str]:
        procs: list[subprocess.Popen] = []
        try:
            prev_stdout = None
            for i, argv in enumerate(argvs):
                is_last = i == len(argvs) - 1
                p = subprocess.Popen(
                    argv, cwd=cwd, env=self._scrubbed_env(), stdin=prev_stdout,
                    stdout=subprocess.PIPE if not is_last else subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, shell=False,
                )
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

    def _run(self, template: str, kind: str, root: Path | None = None) -> str:
        if root is None:
            root = self.repo_root

        try:
            pipelines = self._split_pipelines(self._tokenize(template))
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
            # 'cd' is a shell builtin -- handle it as a cwd change
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
                last_exit, out, err = self._run_pipeline(argvs, cwd)
            except subprocess.TimeoutExpired:
                self.audit.tool_call(tool=f"terminal.{kind}", args={"cmd": template},
                                     result_kind="timeout")
                return f"TIMEOUT after {TIMEOUT_S}s: {template}"
            except FileNotFoundError as e:
                missing = argvs[0][0] if len(argvs) == 1 else str(e)
                return f"ERROR: command not found: {missing}"
            stdout_parts.append(out)
            if err:
                stderr_parts.append(err)

        self.audit.tool_call(tool=f"terminal.{kind}", args={"cmd": template},
                             result_kind=f"exit{last_exit}")
        stdout_tail = "".join(stdout_parts)[-6000:]
        stderr_tail = "\n".join(stderr_parts)[-2000:]
        tail = stdout_tail + (f"\n[stderr]\n{stderr_tail}" if stderr_tail else "")
        return f"exit={last_exit}\n{tail}"

    # --- Helpers -------------------------------------------------------------

    def _repo_tier(self) -> int | None:
        pol = self.repo_root / ".claude" / "repo-policy.yaml"
        try:
            for line in pol.read_text().splitlines():
                m = re.match(r"\s*tier\s*:\s*([0-3])\b", line)
                if m:
                    return int(m.group(1))
        except Exception:
            pass
        return None


def create_server(
    repo_root: Path | str,
    session_id: str = "mcp-terminal",
    actor: str = "terminal-mcp",
) -> TerminalServer:
    repo_root = Path(repo_root).resolve()
    config = get_config()

    policy_engine = PolicyEngine.load(str(repo_root))

    audit_logger = config.get_audit_logger(
        session_id=SessionId.from_string(session_id),
        actor=actor,
        repo=RepoSlug.from_string(repo_root.name),
        tier=Tier(policy_engine.repo.tier),
    )

    return TerminalServer(repo_root, audit_logger, policy_engine, session_id)


async def main() -> None:
    repo_root = Path(os.environ.get("CLAUDE_ENV_REPO_ROOT", os.getcwd())).resolve()
    session_id = os.environ.get("CLAUDE_ENV_SESSION", "mcp-terminal")
    server = create_server(repo_root, session_id)

    async with stdio_server() as (read, write):
        await server.server.run(read, write, server.server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
