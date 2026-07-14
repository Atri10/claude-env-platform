"""
claude-env :: Domain - Value Objects
Common value objects used across domains.

This package replaces the former flat `value_objects.py` module. Every
public name it exported is re-exported here so `from
claudenv.domain.value_objects import X` keeps working identically.
"""
from __future__ import annotations

from claudenv.domain.value_objects._time import utc_now, iso_now
from claudenv.domain.value_objects.enums import Tier, Action, ApprovalDecision, EventType
from claudenv.domain.value_objects.policy_primitives import (
    GlobPattern,
    RegexRule,
    ExtensionRule,
    ContentPattern,
    PolicyRuleSet,
    ContentScanConfig,
)
from claudenv.domain.value_objects.identifiers import (
    NodeId,
    EdgeId,
    ContentHash,
    RepoSlug,
    BranchName,
    TableName,
    RequestId,
    EventId,
    ChunkId,
    SessionId,
)
from claudenv.domain.value_objects.path import Path

__all__ = [
    "utc_now",
    "iso_now",
    "Tier",
    "Action",
    "ApprovalDecision",
    "EventType",
    "GlobPattern",
    "RegexRule",
    "ExtensionRule",
    "ContentPattern",
    "PolicyRuleSet",
    "ContentScanConfig",
    "NodeId",
    "EdgeId",
    "ContentHash",
    "RepoSlug",
    "BranchName",
    "TableName",
    "RequestId",
    "EventId",
    "ChunkId",
    "SessionId",
    "Path",
]
