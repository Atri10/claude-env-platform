"""
claude-env :: Domain - Value Objects - PolicyRuleSet
"""
from __future__ import annotations

from dataclasses import dataclass, field

from claudenv.domain.value_objects.glob_pattern import GlobPattern
from claudenv.domain.value_objects.extension_rule import ExtensionRule
from claudenv.domain.value_objects.regex_rule import RegexRule


@dataclass(frozen=True, slots=True)
class PolicyRuleSet:
    """Collection of policy rules for a scope (global/repo)."""
    deny_paths: list[GlobPattern] = field(default_factory=list)
    deny_extensions: list[ExtensionRule] = field(default_factory=list)
    deny_regex: list[RegexRule] = field(default_factory=list)
    allow_paths: list[GlobPattern] = field(default_factory=list)
    allow_extensions: list[ExtensionRule] = field(default_factory=list)
    override_paths: list[GlobPattern] = field(default_factory=list)
    default_deny: bool = False
