"""
claude-env :: policy engine
File: security/policy_engine.py
Purpose:
    Decide, for a given (repo, path, agent, tier), whether a file may be read,
    and whether its content must be redacted. This is the enforcement core that
    the filesystem MCP server calls before returning any bytes to an agent.

Evaluation order (deny wins, tier-3 default-deny):
    1. global deny (paths / extensions / regex)         -> BLOCK
    2. tier rules (extra deny + default_deny for tier 3)
    3. repo deny (paths / extensions / regex)           -> BLOCK
    4. repo allow (paths / extensions)                  -> ALLOW
    5. fall-through: ALLOW for tier 0-2, BLOCK for tier 3
    6. content scan on the bytes of an allowed file     -> REDACT or BLOCK

Every BLOCK/REDACT decision is returned with the matched rule so the caller can
write a policy_violations audit row.

Usage:
    from security.policy_engine import PolicyEngine
    pe = PolicyEngine.load(repo_root="/path/to/repo",
                           global_policy="~/.claude-env/config/global-policy.yaml")
    decision = pe.evaluate_path("src/auth/token.py")
    if decision.action == "allow":
        text, redactions = pe.scan_content(open(path).read())
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

Action = Literal["allow", "block", "redact"]


def _glob_to_regex(pat: str) -> re.Pattern:
    """gitignore-style glob -> regex.
       **/ matches zero or more leading path segments
       **  matches anything (incl. '/')
       *   matches within a single segment (not '/')
       ?   matches a single non-'/' char
    """
    i, n, out = 0, len(pat), ["^"]
    while i < n:
        if pat[i:i + 3] == "**/":
            out.append("(?:.*/)?")
            i += 3
        elif pat[i:i + 2] == "**":
            out.append(".*")
            i += 2
        elif pat[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pat[i] == "?":
            out.append("[^/]")
            i += 1
        elif pat[i] in ".(){}+|^$\\":
            out.append("\\" + pat[i])
            i += 1
        else:
            out.append(pat[i])
            i += 1
    out.append("$")
    return re.compile("".join(out))


def _pmatch(path: str, pattern: str) -> bool:
    return _glob_to_regex(pattern).match(path) is not None


@dataclass
class Decision:
    action: Action
    reason: str
    rule: str = ""


@dataclass
class CompiledPolicy:
    tier: int
    repo: str
    deny_paths: list[str] = field(default_factory=list)
    deny_ext: list[str] = field(default_factory=list)
    deny_regex: list[tuple[re.Pattern, str]] = field(default_factory=list)
    allow_paths: list[str] = field(default_factory=list)
    allow_ext: list[str] = field(default_factory=list)
    default_deny: bool = False
    content_scan_on: bool = True
    content_on_match: str = "redact"
    content_patterns: list[tuple[str, re.Pattern]] = field(default_factory=list)


class PolicyEngine:
    def __init__(self, repo: CompiledPolicy, glob: CompiledPolicy):
        self.repo = repo
        self.glob = glob

    # -- loading -----------------------------------------------------------
    @staticmethod
    def _read_yaml(path: str | Path) -> dict:
        if yaml is None:
            raise RuntimeError("PyYAML required: pip install pyyaml")
        p = Path(path).expanduser()
        return yaml.safe_load(p.read_text()) if p.exists() else {}

    @classmethod
    def _compile(cls, doc: dict, tier_doc: dict, repo_slug: str) -> CompiledPolicy:
        tier = int(doc.get("tier", 1))
        deny = doc.get("deny", {}) or {}
        allow = doc.get("allow", {}) or {}
        cs = doc.get("content_scan", {}) or {}

        deny_paths = list(deny.get("paths", []))
        deny_ext = list(deny.get("extensions", []))
        deny_regex = [(re.compile(r["pattern"]), r.get("reason", "regex"))
                      for r in deny.get("regex", [])]

        # tier overrides from global tiers map
        trow = (tier_doc.get("tiers", {}) or {}).get(tier, {}) \
            or (tier_doc.get("tiers", {}) or {}).get(str(tier), {})
        deny_ext += list(trow.get("extra_deny_ext", []))
        deny_paths += list(trow.get("extra_deny_paths", []))
        default_deny = bool(trow.get("default_deny", False))

        return CompiledPolicy(
            tier=tier, repo=repo_slug,
            deny_paths=deny_paths, deny_ext=deny_ext, deny_regex=deny_regex,
            allow_paths=list(allow.get("paths", [])),
            allow_ext=list(allow.get("extensions", [])),
            default_deny=default_deny,
            content_scan_on=bool(cs.get("enabled", True)),
            content_on_match=cs.get("on_match", "redact"),
            content_patterns=[(p["name"], re.compile(p["pattern"]))
                              for p in cs.get("patterns", [])],
        )

    @classmethod
    def load(cls, repo_root: str | Path,
             global_policy: str | Path = "~/.claude-env/config/global-policy.yaml"
             ) -> "PolicyEngine":
        gdoc = cls._read_yaml(global_policy)
        rdoc = cls._read_yaml(Path(repo_root) / ".claude" / "repo-policy.yaml")
        if not rdoc:                       # repo declared no policy -> use global tier default
            rdoc = {"tier": gdoc.get("tier", 1), "repo": Path(repo_root).name}
        repo_slug = rdoc.get("repo", Path(repo_root).name)
        repo = cls._compile(rdoc, gdoc, repo_slug)
        glob = cls._compile(gdoc, gdoc, repo_slug)
        return cls(repo, glob)

    # -- path evaluation ---------------------------------------------------
    @staticmethod
    def _match_paths(path: str, globs: list[str]) -> str | None:
        for g in globs:
            if _pmatch(path, g):
                return g
        return None

    @staticmethod
    def _match_ext(path: str, exts: list[str]) -> str | None:
        suffix = Path(path).suffix
        # handle compound like ".env" or files literally named ".env"
        name = Path(path).name
        for e in exts:
            if suffix == e or name == e or name.startswith(e + "."):
                return e
        return None

    def _check_deny(self, path: str, pol: CompiledPolicy, scope: str) -> Decision | None:
        m = self._match_paths(path, pol.deny_paths)
        if m:
            return Decision("block", f"{scope} deny path", m)
        m = self._match_ext(path, pol.deny_ext)
        if m:
            return Decision("block", f"{scope} deny extension", m)
        for rx, reason in pol.deny_regex:
            if rx.search(path):
                return Decision("block", f"{scope} deny regex: {reason}", rx.pattern)
        return None

    def evaluate_path(self, path: str) -> Decision:
        path = path.replace("\\", "/")
        while path.startswith("./"):
            path = path[2:]
        path = path.lstrip("/")

        # 1. global deny
        d = self._check_deny(path, self.glob, "global")
        if d:
            return d
        # 3. repo deny (tier overrides already merged into repo via _compile)
        d = self._check_deny(path, self.repo, "repo")
        if d:
            return d
        # 4. repo allow
        if self._match_paths(path, self.repo.allow_paths) or \
           self._match_ext(path, self.repo.allow_ext):
            return Decision("allow", "matched allow rule")
        # 5. fall-through
        if self.repo.default_deny:         # tier 3
            return Decision("block", "tier-3 default-deny (not in allowlist)", "default_deny")
        return Decision("allow", "no blocking rule; tier default allow")

    # -- content scanning --------------------------------------------------
    def scan_content(self, text: str) -> tuple[str, list[tuple[str, int]]]:
        """Return (possibly-redacted text, [(pattern_name, count), ...])."""
        if not (self.repo.content_scan_on or self.glob.content_scan_on):
            return text, []
        patterns = {n: p for n, p in self.glob.content_patterns}
        patterns.update({n: p for n, p in self.repo.content_patterns})
        hits: list[tuple[str, int]] = []
        out = text
        for name, rx in patterns.items():
            found = rx.findall(out)
            if found:
                hits.append((name, len(found)))
                mode = self.repo.content_on_match or self.glob.content_on_match
                if mode == "block":
                    return "", [(name, -1)]   # signal full block to caller
                out = rx.sub(f"[REDACTED:{name}]", out)
        return out, hits


if __name__ == "__main__":
    import sys
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    pe = PolicyEngine.load(root)
    for test in ["src/app.py", ".env", "config/production.yaml",
                 "docs/adr/001.md", "secrets/key.pem", "data/private/x.csv"]:
        d = pe.evaluate_path(test)
        print(f"{test:35s} -> {d.action:6s} ({d.reason})")
