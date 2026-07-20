"""
claude-env :: Domain - Policy Entities - PolicyEngine
"""
from __future__ import annotations

from typing import Any

from claudenv.domain.incident import is_incident_active
from claudenv.domain.policy.compiled_policy import CompiledPolicy
from claudenv.domain.policy.global_policy import GlobalPolicy
from claudenv.domain.policy.repo_policy import RepoPolicy
from claudenv.domain.policy.results import ContentScanResult, PolicyDecision
from claudenv.domain.value_objects import Path


class PolicyEngine:
    """Pure policy evaluation engine - no I/O, no side effects."""

    def __init__(self, compiled: CompiledPolicy):
        self.compiled = compiled

    @classmethod
    def from_yaml(
            cls,
            global_data: dict[str, Any],
            repo_data: dict[str, Any],
    ) -> PolicyEngine:
        """Create engine from parsed YAML data."""
        global_policy = GlobalPolicy.from_yaml(global_data)
        repo_policy = RepoPolicy.from_yaml(repo_data)
        compiled = repo_policy.to_compiled(global_policy)
        return cls(compiled)

    def evaluate_path(self, path: Path | str) -> PolicyDecision:
        """Evaluate a path against the policy.

        During incident mode (the INCIDENT marker is present) every evaluation
        fails closed: operations must be denied, not merely policy-scored. This
        is the single choke point that makes the filesystem MCP server, the RAG
        indexer, and any other consumer that routes through the engine deny
        everything while incident mode is active.
        """
        if is_incident_active():
            return PolicyDecision.block(
                "incident mode active — all operations denied", rule="INCIDENT"
            )
        if isinstance(path, str):
            path = Path.from_string(path)
        return self.compiled.evaluate(path)

    def scan_content(self, text: str) -> ContentScanResult:
        """Scan content for secrets/PII."""
        return self.compiled.scan_content(text)

    def get_compiled(self) -> CompiledPolicy:
        return self.compiled
