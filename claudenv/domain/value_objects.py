"""
claude-env :: Domain - Value Objects
Common value objects used across domains.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import PurePosixPath
from typing import Any


def utc_now() -> datetime:
    """Current UTC time with timezone info."""
    return datetime.now(timezone.utc)


def iso_now() -> str:
    """Current UTC time as ISO 8601 string."""
    return utc_now().isoformat().replace("+00:00", "Z")


class Tier(int, Enum):
    """Privacy/access tiers (0=public, 3=restricted)."""
    PUBLIC = 0
    INTERNAL = 1
    SENSITIVE = 2
    RESTRICTED = 3

    @classmethod
    def from_string(cls, s: str) -> Tier:
        return cls(int(s))

    @property
    def label(self) -> str:
        return {
            Tier.PUBLIC: "public",
            Tier.INTERNAL: "internal",
            Tier.SENSITIVE: "sensitive",
            Tier.RESTRICTED: "restricted",
        }[self]


class MemoryType(str, Enum):
    """Top-level memory categories."""
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    AGENT = "agent"


class Action(str, Enum):
    """Policy decision actions."""
    ALLOW = "allow"
    BLOCK = "block"
    REDACT = "redact"


class ApprovalDecision(str, Enum):
    """Human approval decisions."""
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


class EventType(str, Enum):
    """Audit event types."""
    AGENT_ACTION = "agent_action"
    TOOL_CALL = "tool_call"
    RETRIEVAL = "retrieval"
    MEMORY_READ = "memory_read"
    MEMORY_WRITE = "memory_write"
    SECURITY_EVENT = "security_event"
    POLICY_VIOLATION = "policy_violation"
    HUMAN_APPROVAL_REQUEST = "human_approval_request"
    HUMAN_APPROVAL_RESOLVE = "human_approval_resolve"


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


@dataclass(frozen=True, slots=True)
class NodeId:
    """Memory node identifier."""
    value: str

    @classmethod
    def generate(cls) -> NodeId:
        return cls(f"mem-{uuid.uuid4().hex}")

    @classmethod
    def from_string(cls, s: str) -> NodeId:
        return cls(s)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class EdgeId:
    """Memory edge identifier."""
    value: str

    @classmethod
    def generate(cls) -> EdgeId:
        return cls(f"edge-{uuid.uuid4().hex}")

    @classmethod
    def from_string(cls, s: str) -> EdgeId:
        return cls(s)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ContentHash:
    """SHA-256 content hash."""
    value: str

    @classmethod
    def compute(cls, data: str | bytes) -> ContentHash:
        if isinstance(data, str):
            data = data.encode("utf-8")
        return cls(hashlib.sha256(data).hexdigest())

    @classmethod
    def from_string(cls, s: str) -> ContentHash:
        return cls(s)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class RepoSlug:
    """Repository slug (namespace-safe identifier)."""
    value: str

    @classmethod
    def generate(cls, name: str) -> RepoSlug:
        s = re.sub(r"[^a-z0-9._-]+", "-", name.strip().lower())
        s = re.sub(r"-{2,}", "-", s).strip("-.")
        return cls(s or "repo")

    @classmethod
    def from_string(cls, s: str) -> RepoSlug:
        return cls(s)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class BranchName:
    """Git branch name."""
    value: str

    @classmethod
    def from_string(cls, s: str) -> BranchName:
        return cls(s)

    @classmethod
    def default(cls) -> BranchName:
        return cls("main")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class TableName:
    """LanceDB table name (repo__branch)."""
    repo: RepoSlug
    branch: BranchName

    @classmethod
    def generate(cls, repo: RepoSlug, branch: BranchName) -> TableName:
        safe = lambda s: s.replace("/", "-").replace(" ", "_")
        return cls(RepoSlug(f"{safe(str(repo))}__{safe(str(branch))}"), BranchName(""))

    def __str__(self) -> str:
        return str(self.repo)


@dataclass(frozen=True, slots=True)
class RequestId:
    """Approval request identifier."""
    value: str

    @classmethod
    def generate(cls) -> RequestId:
        return cls(f"appr-{secrets.token_urlsafe(12)}")

    @classmethod
    def from_string(cls, s: str) -> RequestId:
        return cls(s)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class EventId:
    """Audit event identifier."""
    value: str

    @classmethod
    def generate(cls) -> EventId:
        return cls(f"evt-{uuid.uuid4().hex[:12]}")

    @classmethod
    def from_string(cls, s: str) -> EventId:
        return cls(s)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ChunkId:
    """RAG chunk identifier."""
    value: str

    @classmethod
    def from_parts(cls, repo: str, path: str, start: int, end: int) -> ChunkId:
        import hashlib
        raw = f"{repo}:{path}:{start}:{end}"
        return cls(hashlib.sha1(raw.encode()).hexdigest())

    @classmethod
    def from_string(cls, s: str) -> ChunkId:
        return cls(s)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class SessionId:
    """Session identifier."""
    value: str

    @classmethod
    def generate(cls) -> SessionId:
        return cls(f"sess-{uuid.uuid4().hex[:12]}")

    @classmethod
    def from_string(cls, s: str) -> SessionId:
        return cls(s)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class Path:
    """Repository-relative path."""
    value: str

    @classmethod
    def from_string(cls, s: str) -> Path:
        # Normalize: strip leading ./ and /, convert backslashes
        normalized = s.replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        normalized = normalized.lstrip("/")
        return cls(normalized)

    @classmethod
    def scratch(cls, subpath: str) -> Path:
        return cls(f"scratch://{subpath.lstrip('/')}")

    @property
    def is_scratch(self) -> bool:
        return self.value.startswith("scratch://")

    @property
    def parts(self) -> tuple[str, ...]:
        return tuple(PurePosixPath(self.value).parts)

    def parent(self) -> Path:
        return Path(str(PurePosixPath(self.value).parent))

    def join(self, *parts: str) -> Path:
        return Path(str(PurePosixPath(self.value).joinpath(*parts)))

    def __str__(self) -> str:
        return self.value

    def __truediv__(self, other: str) -> Path:
        return self.join(other)
