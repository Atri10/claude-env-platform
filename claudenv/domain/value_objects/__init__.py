"""
claude-env :: Domain - Value Objects
Common value objects used across domains.

This package replaces the former flat `value_objects.py` module. Every
public name it exported is re-exported here so `from
claudenv.domain.value_objects import X` keeps working identically.
"""
from __future__ import annotations

from claudenv.domain.value_objects._time import iso_now, utc_now
from claudenv.domain.value_objects.enums import Action, ApprovalDecision, EventType, Tier
from claudenv.domain.value_objects.identifiers import (
    BranchName,
    ChunkId,
    ContentHash,
    EdgeId,
    EventId,
    NodeId,
    RepoSlug,
    RequestId,
    SessionId,
    TableName,
)
from claudenv.domain.value_objects.path import Path
from claudenv.domain.value_objects.policy_primitives import (
    ContentPattern,
    ContentScanConfig,
    ExtensionRule,
    GlobPattern,
    PolicyRuleSet,
    RegexRule,
)

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
