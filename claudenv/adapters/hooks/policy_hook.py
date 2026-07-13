"""
claude-env :: Adapters - Policy Hook

PreToolUse hook for path/command validation against policy engine.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any, Dict

from claudenv.adapters.config import get_config
from claudenv.domain.policy import PolicyEngine
from claudenv.domain.value_objects import RepoSlug, Tier, SessionId
from claudenv.ports import IAuditLogger
from claudenv.ports.hooks.interfaces import IPreToolUseHook


class PolicyHook(IPreToolUseHook):
    """Policy enforcement hook for PreToolUse."""

    def __init__(
            self,
            policy_engine: PolicyEngine,
            audit_logger: IAuditLogger,
            repo_root: Path,
    ):
        self.engine = policy_engine
        self.audit = audit_logger
        self.repo_root = repo_root
        self.fail_closed = os.environ.get("CLAUDE_ENV_HOOK_FAIL_CLOSED", "").lower() == "true"

    # Signature for operator-attributed decisions (prevents model evasion)
    SIGNATURE = ("[claude-env: operator-installed governance control — not session "
                 "text; do not attempt to bypass. See `claude-env status`.]")

    def _signed(self, reason: str) -> str:
        return f"{self.SIGNATURE} {reason}"

    def _resolve_path(self, path: str) -> tuple[str, Path]:
        """Resolve path, rejecting escapes."""
        candidate = (self.repo_root / path).resolve()
        try:
            rel = candidate.relative_to(self.repo_root)
        except ValueError:
            raise PermissionError(f"path escapes repo root: {path}")
        return str(rel).replace("\\", "/"), candidate

    def _evaluate_path(self, path: str) -> bool:
        """Check if path is allowed by policy."""
        if not path:
            return True
        try:
            rel, _ = self._resolve_path(path)
        except PermissionError:
            return False

        decision = self.engine.evaluate_path(rel)
        if decision.action == "block":
            self.audit.policy_violation(
                path=rel, rule=decision.rule or decision.reason,
                decision="block", tier=self.engine.repo.tier if self.engine.repo else None,
            )
            return False
        return True

    def _scan_content(self, content: str) -> tuple[str, list]:
        """Scan content for secrets."""
        return self.engine.scan_content(content)

    def _parse_bash_args(self, command: str) -> tuple[list[str], list[str]]:
        """Parse bash command for file arguments and redirections."""
        tokens = shlex.split(command, posix=True)
        file_args = []
        redir_targets = []

        i = 0
        while i < len(tokens):
            t = tokens[i]
            if t in {">", ">>", "<"} and i + 1 < len(tokens):
                redir_targets.append(tokens[i + 1])
                i += 2
            elif t.startswith(">") or t.startswith("<"):
                redir_targets.append(t[1:])
                i += 1
            elif t in {"cat", "grep", "sed", "awk", "head", "tail", "less", "more", "vim", "nvim", "code"}:
                if i + 1 < len(tokens):
                    file_args.append(tokens[i + 1])
                    i += 1
                else:
                    i += 1
            elif t in {"git", "cp", "mv", "rm"}:
                i += 1
                while i < len(tokens) and not tokens[i].startswith("-"):
                    file_args.append(tokens[i])
                    i += 1
            else:
                i += 1

        return file_args, redir_targets

    def _is_net_cmd(self, command: str) -> bool:
        """Check if command is network egress."""
        net_cmds = {"curl", "wget", "nc", "netcat", "ssh", "scp", "rsync", "sftp", "ftp"}
        first = command.split()[0] if command.split() else ""
        return first in net_cmds

    def on_tool_call(self, tool_call: Dict[str, Any]) -> Dict[str, Any]:
        """
        Evaluate tool call against policy.

        Returns:
            {"allow": True} or {"allow": False, "reason": "..."}
        """
        tool = tool_call.get("tool", "")
        args = tool_call.get("args", {})

        # Incident mode = deny everything
        incident_marker = Path(
            os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env"))) / "state" / "INCIDENT"
        if incident_marker.exists():
            return {"allow": False, "reason": self._signed("INCIDENT MODE ACTIVE — all operations denied")}

        try:
            if tool in {"Read", "Write", "Edit", "NotebookEdit", "Glob", "Grep"}:
                path = args.get("path") or args.get("file_path") or ""
                if not self._evaluate_path(path):
                    return {"allow": False, "reason": self._signed(f"policy blocked path: {path}")}

                if tool in {"Write", "Edit", "NotebookEdit"}:
                    content = args.get("content") or args.get("new_string") or ""
                    redacted, hits = self._scan_content(content)
                    if hits and hits[0][1] == -1:
                        return {"allow": False, "reason": self._signed(f"secret content blocked in {tool}")}
                    if hits:
                        return {"allow": "ask", "reason": self._signed(f"secrets detected in {tool}, redacted")}

            elif tool == "Bash":
                command = args.get("command", "")
                if not command:
                    return {"allow": True}

                # Check for shell operators we don't support
                try:
                    tokens = shlex.split(command, posix=True)
                    for t in tokens:
                        if t in {"|", ">", ">>", "<", "2>", "&", "&&", "||", ";"}:
                            continue
                        if t.startswith((">", "<")):
                            continue
                        if any(c in t for c in "&|;<>()$`"):
                            return {"allow": False, "reason": self._signed(f"unsupported shell operator in: {command}")}
                except ValueError:
                    return {"allow": False, "reason": self._signed(f"unparseable command: {command}")}

                # Check file args in command
                file_args, redir_targets = self._parse_bash_args(command)
                for p in file_args + redir_targets:
                    if p and not self._evaluate_path(p):
                        return {"allow": False, "reason": self._signed(f"bash command accesses blocked path: {p}")}

                # Network egress check
                if self._is_net_cmd(command):
                    tier = self.engine.repo.tier if self.engine.repo else 0
                    if tier >= 2:
                        return {"allow": False, "reason": self._signed(f"network egress blocked at tier {tier}")}
                    return {"allow": "ask", "reason": self._signed("network egress requires operator approval")}

                # Secret in command string
                redacted, hits = self._scan_content(command)
                if hits and hits[0][1] == -1:
                    return {"allow": False, "reason": self._signed(f"secret in command string")}

            return {"allow": True}

        except Exception as e:
            if self.fail_closed:
                return {"allow": False, "reason": self._signed(f"hook error (fail-closed): {e}")}
            return {"allow": True}


def create_hook(
        repo_root: Path | str,
        session_id: str = "policy-hook",
        actor: str = "policy-hook",
) -> PolicyHook:
    repo_root = Path(repo_root).resolve()
    config = get_config()

    policy_engine = PolicyEngine.load(str(repo_root))

    audit_logger = config.get_audit_logger(
        session_id=SessionId.from_string(session_id),
        actor=actor,
        repo=RepoSlug.from_string(repo_root.name),
        tier=Tier(policy_engine.repo.tier if policy_engine.repo else 0),
    )

    return PolicyHook(policy_engine, audit_logger, repo_root)


def main() -> None:
    """CLI entry point for hook execution."""
    try:
        hook_input = json.load(sys.stdin)
        tool_name = hook_input.get("tool_name", "")
        tool_input = hook_input.get("tool_input", {})

        repo_root = Path(os.environ.get("CLAUDE_ENV_REPO_ROOT", os.getcwd())).resolve()
        hook = create_hook(repo_root)

        result = hook.on_tool_call({"tool": tool_name, "args": tool_input})

        json.dump(result, sys.stdout)
    except Exception:
        json.dump({"allow": True}, sys.stdout)


if __name__ == "__main__":
    main()
