"""
claude-env :: Domain - Value Objects - GlobPattern
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
