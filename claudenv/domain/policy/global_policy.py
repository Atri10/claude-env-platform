"""
claude-env :: Domain - Policy Entities - GlobalPolicy
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from claudenv.domain.value_objects import (
    ExtensionRule,
    GlobPattern,
    RegexRule,
    Tier,
)


@dataclass(frozen=True, slots=True)
class GlobalPolicy:
    """Global policy configuration (from ~/.claude-env/config/global-policy.yaml)."""
    tier: Tier = Tier.INTERNAL
    deny_paths: tuple[GlobPattern, ...] = ()
    deny_extensions: tuple[ExtensionRule, ...] = ()
    deny_regex: tuple[RegexRule, ...] = ()
    allow_paths: tuple[GlobPattern, ...] = ()
    allow_extensions: tuple[ExtensionRule, ...] = ()
    tiers: dict[Tier, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, data: dict[str, Any]) -> GlobalPolicy:
        tier = Tier.from_string(data.get("tier", 1))

        deny_paths = tuple(GlobPattern(p) for p in data.get("deny", {}).get("paths", []))
        deny_exts = tuple(ExtensionRule(e) for e in data.get("deny", {}).get("extensions", []))
        deny_regex = tuple(RegexRule.create(r["pattern"], r.get("reason", "regex"))
                           for r in data.get("deny", {}).get("regex", []))
        allow_paths = tuple(GlobPattern(p) for p in data.get("allow", {}).get("paths", []))
        allow_exts = tuple(ExtensionRule(e) for e in data.get("allow", {}).get("extensions", []))

        # Parse tiers
        tiers: dict[Tier, dict[str, Any]] = {}
        for k, v in data.get("tiers", {}).items():
            tier_key = Tier.from_string(k)
            tiers[tier_key] = v

        return cls(
            tier=tier,
            deny_paths=deny_paths,
            deny_extensions=deny_exts,
            deny_regex=deny_regex,
            allow_paths=allow_paths,
            allow_extensions=allow_exts,
            tiers=tiers,
        )
