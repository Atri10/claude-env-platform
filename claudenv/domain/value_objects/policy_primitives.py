"""
claude-env :: Domain - Value Objects - Policy primitives

Groups: GlobPattern, RegexRule, ExtensionRule, ContentPattern, PolicyRuleSet,
ContentScanConfig.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class GlobPattern:
    """Gitignore-style glob pattern with compiled regex."""
    pattern: str
    _regex: re.Pattern = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_regex", self._compile(self.pattern))

    @staticmethod
    def _compile(pattern: str) -> re.Pattern:
        i, n, out = 0, len(pattern), ["^"]
        while i < n:
            if pattern[i:i + 3] == "**/":
                out.append("(?:.*/)?")
                i += 3
            elif pattern[i:i + 2] == "**":
                out.append(".*")
                i += 2
            elif pattern[i] == "*":
                out.append("[^/]*")
                i += 1
            elif pattern[i] == "?":
                out.append("[^/]")
                i += 1
            elif pattern[i] in ".(){}+|^$\\":
                out.append("\\" + pattern[i])
                i += 1
            else:
                out.append(pattern[i])
                i += 1
        out.append("$")
        return re.compile("".join(out))

    def matches(self, path: str) -> bool:
        return self._regex.match(path) is not None

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, GlobPattern):
            return NotImplemented
        return self.pattern == other.pattern

    def __hash__(self) -> int:
        return hash(self.pattern)

    def __str__(self) -> str:
        return self.pattern


@dataclass(frozen=True, slots=True)
class RegexRule:
    """Compiled regex rule with reason."""
    pattern: re.Pattern
    reason: str

    @classmethod
    def create(cls, pattern: str, reason: str) -> RegexRule:
        return cls(re.compile(pattern), reason)

    def matches(self, path: str) -> bool:
        return self.pattern.search(path) is not None

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RegexRule):
            return NotImplemented
        return (self.pattern.pattern, self.reason) == (other.pattern.pattern, other.reason)

    def __hash__(self) -> int:
        return hash((self.pattern.pattern, self.reason))

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class ExtensionRule:
    """File extension matching rule."""
    extension: str  # e.g., ".env", ".pem"

    def matches(self, path: str) -> bool:
        from pathlib import Path as PathLib
        path_obj = PathLib(path)
        suffix = path_obj.suffix
        name = path_obj.name

        # Normal extension match
        if suffix == self.extension:
            return True
        # Exact filename match (e.g., ".env")
        if name == self.extension:
            return True
        # Compound extensions (e.g., ".env.local")
        if name.startswith(self.extension + "."):
            return True
        # Dotfile variants (e.g., "..env")
        bare_name = name.lstrip(".")
        bare_ext = self.extension.lstrip(".")
        if bare_name == bare_ext:
            return True
        if bare_name.startswith(bare_ext + "."):
            return True
        return False


@dataclass(frozen=True, slots=True)
class ContentPattern:
    """Content scanning pattern (for secrets/PII)."""
    name: str
    pattern: re.Pattern

    @classmethod
    def create(cls, name: str, pattern: str) -> ContentPattern:
        return cls(name, re.compile(pattern))

    def findall(self, text: str) -> list[str]:
        return self.pattern.findall(text)

    def sub(self, text: str, replacement: str) -> str:
        return self.pattern.sub(replacement, text)


@dataclass(frozen=True, slots=True)
class PolicyRuleSet:
    """Collection of policy rules for a scope (global/repo)."""
    deny_paths: list[GlobPattern] = field(default_factory=list)
    deny_extensions: list[ExtensionRule] = field(default_factory=list)
    deny_regex: list[RegexRule] = field(default_factory=list)
    allow_paths: list[GlobPattern] = field(default_factory=list)
    allow_extensions: list[ExtensionRule] = field(default_factory=list)
    override_paths: list[GlobPattern] = field(default_factory=list)
    default_deny: bool = False


@dataclass(frozen=True, slots=True)
class ContentScanConfig:
    """Content scanning configuration."""
    enabled: bool = True
    on_match: str = "redact"  # "redact" or "block"
    patterns: list[ContentPattern] = field(default_factory=list)
