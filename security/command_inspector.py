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

    # Resolve exempt_root itself once, up front, so it compares correctly
    # against resolved candidate paths below. On macOS (and some Linux temp
    # setups) tempfile.mkdtemp() returns a path under a symlink (e.g.
    # /var/folders/... -> /private/var/folders/...); comparing an unresolved
    # exempt_root against a resolved candidate would silently never match,
    # defeating the exemption.
    exempt_root_resolved = None
    if exempt_root is not None:
        try:
            exempt_root_resolved = Path(exempt_root).resolve()
        except OSError:
            exempt_root_resolved = exempt_root

    # 1. any file argument the policy blocks -> deny (hard), unless it
    #    resolves inside exempt_root (a scratch pad's own directory).
    for tok in paths:
        abs_tok = tok if os.path.isabs(os.path.expanduser(tok)) \
            else str((Path(cwd or ".") / tok))
        if exempt_root_resolved is not None:
            try:
                resolved = Path(abs_tok).resolve()
                if resolved == exempt_root_resolved or exempt_root_resolved in resolved.parents:
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
