"""
claude-env :: Ports - Policy engine interface
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Protocol

from claudenv.domain.policy import CompiledPolicy, ContentScanResult, PolicyDecision


class IPolicyEngine(Protocol):
    """Policy evaluation engine."""

    @abstractmethod
    def evaluate_path(self, path: str) -> PolicyDecision:
        ...

    @abstractmethod
    def scan_content(self, text: str) -> ContentScanResult:
        ...

    @abstractmethod
    def compile(self, repo_policy: dict, global_policy: dict) -> CompiledPolicy:
        ...
