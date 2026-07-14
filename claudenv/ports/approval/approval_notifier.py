"""
claude-env :: Ports - Approval notifier interface
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Protocol

from claudenv.domain.value_objects import RequestId


class IApprovalNotifier(Protocol):
    """Approval notification delivery."""

    @abstractmethod
    def notify(self, request_id: RequestId) -> None:
        ...
