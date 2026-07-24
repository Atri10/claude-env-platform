"""
claude-env :: Adapters - SQLite Persistence - Policy Repository

SQLite implementation of ``IPolicyRepository``. Persists global and repo
policies as JSON blobs.
"""
from __future__ import annotations

import json
from typing import Any

from claudenv.domain.policy import RepoPolicy
from claudenv.ports import IPolicyRepository

from .database import SQLiteDatabase


class SQLitePolicyRepository(IPolicyRepository):
    """SQLite policy configuration repository."""

    def __init__(self, db: SQLiteDatabase):
        self._db = db

    def get_global_policy(self) -> dict[str, Any]:
        row = self._db.query_one(
            "SELECT policy_json FROM global_policy WHERE id = 1"
        )
        if row:
            return json.loads(row["policy_json"])
        return {}

    def get_repo_policy(self, repo_root: str) -> RepoPolicy | None:
        row = self._db.query_one(
            "SELECT policy_json FROM repo_policy WHERE repo_root = ?",
            (repo_root,),
        )
        if not row:
            return None
        return RepoPolicy.from_yaml(json.loads(row["policy_json"]))

    def save_repo_policy(self, repo_root: str, policy: RepoPolicy) -> None:
        self._db.execute(
            "INSERT INTO repo_policy (repo_root, policy_json) VALUES (?, ?) "
            "ON CONFLICT(repo_root) DO UPDATE SET policy_json = excluded.policy_json",
            (repo_root, json.dumps(policy.to_dict() if hasattr(policy, 'to_dict') else {
                "version": policy.version,
                "tier": int(policy.tier),
                "repo": str(policy.repo),
                "description": policy.description,
                "allow": {
                    "paths": [str(p) for p in policy.allow_paths],
                    "extensions": [str(e) for e in policy.allow_extensions],
                },
                "deny": {
                    "paths": [str(p) for p in policy.deny_paths],
                    "extensions": [str(e) for e in policy.deny_extensions],
                    "regex": [{"pattern": r.pattern.pattern, "reason": r.reason} for r in policy.deny_regex],
                },
                "override_deny": [str(p) for p in policy.override_deny],
                "content_scan": {
                    "enabled": policy.content_scan.enabled,
                    "on_match": policy.content_scan.on_match,
                    "patterns": [{"name": p.name, "pattern": p.pattern.pattern} for p in policy.content_scan.patterns],
                },
                "rag": {
                    "enabled": policy.rag_enabled,
                    "index_paths": [str(p) for p in policy.rag_index_paths],
                    "exclude_paths": [str(p) for p in policy.rag_exclude_paths],
                    "index_only_committed": policy.rag_only_committed,
                },
                "memory": {
                    "namespace": policy.memory_namespace,
                    "isolated": policy.memory_isolated,
                    "share_with_agents": list(policy.memory_share_with),
                },
                "agent_permissions": policy.agent_permissions,
            })),
        )
