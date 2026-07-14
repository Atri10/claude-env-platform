"""
claude-env :: Domain - Value Objects - Path
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath


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
