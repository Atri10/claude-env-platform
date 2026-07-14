"""
claude-env :: Domain - Value Objects
Common value objects used across domains.

This package replaces the former flat `value_objects.py` module. Every
public name it exported is re-exported here so `from
claudenv.domain.value_objects import X` keeps working identically.
"""
from __future__ import annotations

from claudenv.domain.value_objects._time import utc_now, iso_now
from claudenv.domain.value_objects.tier import Tier
from claudenv.domain.value_objects.action import Action
from claudenv.domain.value_objects.approval_decision import ApprovalDecision
from claudenv.domain.value_objects.event_type import EventType
from claudenv.domain.value_objects.glob_pattern import GlobPattern
from claudenv.domain.value_objects.regex_rule import RegexRule
from claudenv.domain.value_objects.extension_rule import ExtensionRule
from claudenv.domain.value_objects.content_pattern import ContentPattern
from claudenv.domain.value_objects.policy_rule_set import PolicyRuleSet
from claudenv.domain.value_objects.content_scan_config import ContentScanConfig
from claudenv.domain.value_objects.node_id import NodeId
from claudenv.domain.value_objects.edge_id import EdgeId
from claudenv.domain.value_objects.content_hash import ContentHash
from claudenv.domain.value_objects.repo_slug import RepoSlug
from claudenv.domain.value_objects.branch_name import BranchName
from claudenv.domain.value_objects.table_name import TableName
from claudenv.domain.value_objects.request_id import RequestId
from claudenv.domain.value_objects.event_id import EventId
from claudenv.domain.value_objects.chunk_id import ChunkId
from claudenv.domain.value_objects.session_id import SessionId
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
