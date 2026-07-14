"""
claude-env :: Domain - Policy Entities - RepoPolicy
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from claudenv.domain.policy.compiled_policy import CompiledPolicy
from claudenv.domain.policy.global_policy import GlobalPolicy
from claudenv.domain.value_objects import (
    ContentPattern,
    ContentScanConfig,
    ExtensionRule,
    GlobPattern,
    PolicyRuleSet,
    RegexRule,
    RepoSlug,
    Tier,
)


@dataclass(frozen=True, slots=True)
class RepoPolicy:
    """Repository policy configuration (from .claude/repo-policy.yaml)."""
    version: int
    tier: Tier
    repo: RepoSlug
    description: str = ""

    # Allow rules
    allow_paths: tuple[GlobPattern, ...] = ()
    allow_extensions: tuple[ExtensionRule, ...] = ()

    # Deny rules
    deny_paths: tuple[GlobPattern, ...] = ()
    deny_extensions: tuple[ExtensionRule, ...] = ()
    deny_regex: tuple[RegexRule, ...] = ()

    # Override deny (repo-only escape hatch)
    override_deny: tuple[GlobPattern, ...] = ()

    # Content scanning
    content_scan: ContentScanConfig = field(default_factory=ContentScanConfig)

    # RAG settings
    rag_enabled: bool = True
    rag_index_paths: tuple[GlobPattern, ...] = (GlobPattern("**"),)
    rag_exclude_paths: tuple[GlobPattern, ...] = ()
    rag_only_committed: bool = True

    # Memory settings
    memory_namespace: str = ""
    memory_isolated: bool = False
    memory_share_with: tuple[str, ...] = ()

    # Agent permissions (overrides)
    agent_permissions: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, data: dict[str, Any]) -> RepoPolicy:
        """Create from parsed YAML."""
        tier = Tier.from_string(data.get("tier", 1))
        repo_slug = RepoSlug.from_string(data.get("repo", "repo"))

        # Build allow rules
        allow_paths = tuple(GlobPattern(p) for p in data.get("allow", {}).get("paths", []))
        allow_exts = tuple(ExtensionRule(e) for e in data.get("allow", {}).get("extensions", []))

        # Build deny rules
        deny_paths = tuple(GlobPattern(p) for p in data.get("deny", {}).get("paths", []))
        deny_exts = tuple(ExtensionRule(e) for e in data.get("deny", {}).get("extensions", []))
        deny_regex = tuple(RegexRule.create(r["pattern"], r.get("reason", "regex"))
                           for r in data.get("deny", {}).get("regex", []))

        # Override deny
        override_deny = tuple(GlobPattern(p) for p in data.get("override_deny", []))

        # Content scan
        cs = data.get("content_scan", {})
        content_scan = ContentScanConfig(
            enabled=cs.get("enabled", True),
            on_match=cs.get("on_match", "redact"),
            patterns=tuple(
                ContentPattern.create(p["name"], p["pattern"])
                for p in cs.get("patterns", [])
            ),
        )

        # RAG
        rag = data.get("rag", {})
        rag_enabled = rag.get("enabled", tier < Tier.RESTRICTED)
        rag_index = tuple(GlobPattern(p) for p in rag.get("index_paths", ["**"]))
        rag_exclude = tuple(GlobPattern(p) for p in rag.get("exclude_paths", []))
        rag_only_committed = rag.get("index_only_committed", True)

        # Memory
        mem = data.get("memory", {})
        memory_namespace = mem.get("namespace", f"proj-{repo_slug}")
        memory_isolated = mem.get("isolated", tier >= Tier.SENSITIVE)
        memory_share_with = tuple(mem.get("share_with_agents", []))

        # Agent permissions
        agent_perms = data.get("agent_permissions", {})

        return cls(
            version=data.get("version", 1),
            tier=tier,
            repo=repo_slug,
            description=data.get("description", ""),
            allow_paths=allow_paths,
            allow_extensions=allow_exts,
            deny_paths=deny_paths,
            deny_extensions=deny_exts,
            deny_regex=deny_regex,
            override_deny=override_deny,
            content_scan=content_scan,
            rag_enabled=rag_enabled,
            rag_index_paths=rag_index,
            rag_exclude_paths=rag_exclude,
            rag_only_committed=rag_only_committed,
            memory_namespace=memory_namespace,
            memory_isolated=memory_isolated,
            memory_share_with=memory_share_with,
            agent_permissions=agent_perms,
        )

    def to_compiled(self, global_policy: GlobalPolicy) -> CompiledPolicy:
        """Compile with global policy."""
        # Merge tier overrides from global
        tier_overrides = global_policy.tiers.get(self.tier, {})
        extra_deny_ext = tuple(ExtensionRule(e) for e in tier_overrides.get("extra_deny_extensions", []))
        extra_deny_paths = tuple(GlobPattern(p) for p in tier_overrides.get("extra_deny_paths", []))
        default_deny = tier_overrides.get("default_deny", False)

        repo_rules = PolicyRuleSet(
            deny_paths=list(self.deny_paths) + list(extra_deny_paths),
            deny_extensions=list(self.deny_extensions) + list(extra_deny_ext),
            deny_regex=list(self.deny_regex),
            allow_paths=list(self.allow_paths),
            allow_extensions=list(self.allow_extensions),
            override_paths=list(self.override_deny),
            default_deny=default_deny,
        )

        global_rules = PolicyRuleSet(
            deny_paths=list(global_policy.deny_paths),
            deny_extensions=list(global_policy.deny_extensions),
            deny_regex=list(global_policy.deny_regex),
            allow_paths=list(global_policy.allow_paths),
            allow_extensions=list(global_policy.allow_extensions),
        )

        return CompiledPolicy(
            repo=self.repo,
            tier=self.tier,
            global_rules=global_rules,
            repo_rules=repo_rules,
            content_scan=self.content_scan,
            override_deny=self.override_deny,
        )
