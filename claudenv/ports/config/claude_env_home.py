"""
claude-env :: Ports - claude-env home directory location interface
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Protocol


class IClaudeEnvHome(Protocol):
    """claude-env home directory location."""

    @abstractmethod
    def get_claude_env_home(self) -> str:
        ...
