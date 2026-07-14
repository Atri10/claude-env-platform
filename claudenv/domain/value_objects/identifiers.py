"""
claude-env :: Domain - Value Objects - Identifiers

Groups the simple identifier/name value objects: NodeId, EdgeId, EventId,
RequestId, ChunkId, SessionId, ContentHash, RepoSlug, BranchName, TableName.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from dataclasses import dataclass


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
class ChunkId:
    """RAG chunk identifier."""
    value: str

    @classmethod
    def from_parts(cls, repo: str, path: str, start: int, end: int) -> ChunkId:
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
