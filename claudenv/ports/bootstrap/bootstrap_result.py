"""
claude-env :: Ports - Bootstrap result value object
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BootstrapResult:
    """Result of a bootstrap step."""

    success: bool = False
    message: str = ""
    warnings: list[str] = field(default_factory=list)
    step_name: str = ""

    @classmethod
    def ok(cls, message: str, warnings: list[str] | None = None, step_name: str = "") -> BootstrapResult:
        return cls(success=True, message=message, warnings=warnings or [], step_name=step_name)

    @classmethod
    def failure(cls, message: str, warnings: list[str] | None = None, step_name: str = "") -> BootstrapResult:
        return cls(success=False, message=message, warnings=warnings or [], step_name=step_name)
