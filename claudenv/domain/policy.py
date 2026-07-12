"""
claude-env :: Domain - Policy Entities
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from claudenv.domain.value_objects import (
    Action, ContentHash, ContentPattern, Path, GlobPattern, RegexRule,
    RepoSlug, Tier, ExtensionRule, PolicyRuleSet, ContentScanConfig,
)


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """Result of a policy evaluation."""
    action: Action
    reason: str
    rule: str = ""

    @classmethod
    def allow(cls, reason: str = "no blocking rule") -> PolicyDecision:
        return cls(Action.ALLOW, reason)

    @classmethod
    def block(cls, reason: str, rule: str = "") -> PolicyDecision:
        return cls(Action.BLOCK, reason, rule)

    @classmethod
    def redact(cls, reason: str, rule: str = "") -> PolicyDecision:
        return cls(Action.REDACT, reason, rule)

    @property
    def is_allowed(self) -> bool:
        return self.action == Action.ALLOW


@dataclass(frozen=True, slots=True)
class ContentScanResult:
    """Result of content scanning."""
    text: str
    hits: list[tuple[str, int]]  # (pattern_name, count), count=-1 means block

    @property
    def is_blocked(self) -> bool:
        return any(count == -1 for _, count in self.hits)

    @property
    def has_redactions(self) -> bool:
        return len(self.hits) > 0 and not self.is_blocked


@dataclass(frozen=True, slots=True)
class CompiledPolicy:
    """Fully compiled policy ready for evaluation."""
    repo: RepoSlug
    tier: Tier
    global_rules: PolicyRuleSet
    repo_rules: PolicyRuleSet
    content_scan: ContentScanConfig
    override_deny: tuple[GlobPattern, ...] = ()

    def evaluate(self, path: Path | str) -> PolicyDecision:
        """Evaluate a path against the compiled policy."""
        rel_path = str(path)

        # 0. Override deny (repo-specific allow-list that beats global/repo deny)
        for pattern in self.override_deny:
            if pattern.matches(rel_path):
                return PolicyDecision.allow(f"override_deny: {pattern}")

        # 1. Global deny
        decision = self._check_deny(rel_path, self.global_rules, "global")
        if decision:
            return decision

        # 2. Repo deny (includes tier overrides)
        decision = self._check_deny(rel_path, self.repo_rules, "repo")
        if decision:
            return decision

        # 3. Repo allow
        if self._check_allow(rel_path, self.repo_rules):
            return PolicyDecision.allow("matched allow rule")

        # 4. Fall-through
        if self.repo_rules.default_deny:
            return PolicyDecision.block("tier default-deny (not in allowlist)", "default_deny")
        return PolicyDecision.allow("no blocking rule; tier default allow")

    def _check_deny(self, path: str, rules: PolicyRuleSet, scope: str) -> PolicyDecision | None:
        for pattern in rules.deny_paths:
            if pattern.matches(path):
                return PolicyDecision.block(f"{scope} deny path", str(pattern))
        for ext_rule in rules.deny_extensions:
            if ext_rule.matches(path):
                return PolicyDecision.block(f"{scope} deny extension", ext_rule.extension)
        for regex in rules.deny_regex:
            if regex.matches(path):
                return PolicyDecision.block(f"{scope} deny regex: {regex.reason}", regex.pattern.pattern)
        return None

    def _check_allow(self, path: str, rules: PolicyRuleSet) -> bool:
        for pattern in rules.allow_paths:
            if pattern.matches(path):
                return True
        for ext_rule in rules.allow_extensions:
            if ext_rule.matches(path):
                return True
        return False

    def scan_content(self, text: str) -> tuple[str, list[tuple[str, int]]]:
        """Scan content for secrets/PII. Returns (redacted_text, hits)."""
        if not self.content_scan.enabled:
            return text, []

        hits: list[tuple[str, int]] = []
        out = text
        block_mode = self.content_scan.on_match == "block"

        for pattern in self.content_scan.patterns:
            found = pattern.findall(out)
            if found:
                hits.append((pattern.name, len(found)))
                if block_mode:
                    return "", [(pattern.name, -1)]  # -1 signals full block
                out = pattern.sub(out, f"[REDACTED:{pattern.name}]")

        return out, hits


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
        """Evaluate a path against the policy."""
        if isinstance(path, str):
            path = Path.from_string(path)
        return self.compiled.evaluate(path)

    def scan_content(self, text: str) -> ContentScanResult:
        """Scan content for secrets/PII."""
        return self.compiled.scan_content(text)

    def get_compiled(self) -> CompiledPolicy:
        return self.compiled


class PolicyService:
    """Application service for policy operations."""

    def __init__(self, config: Any):  # IConfigProvider
        self._config = config

    def get_global_policy(self) -> GlobalPolicy:
        data = self._config.get_global_policy()
        return GlobalPolicy.from_yaml(data)

    def get_repo_policy(self, repo_root: str) -> RepoPolicy | None:
        repo_yaml = Path(repo_root) / ".claude" / "repo-policy.yaml"
        if not repo_yaml.exists():
            return None
        import yaml
        data = yaml.safe_load(repo_yaml.read_text()) or {}
        return RepoPolicy.from_yaml(data)

    def load_engine(self, repo_root: str) -> PolicyEngine:
        repo_policy = self.get_repo_policy(repo_root)
        if not repo_policy:
            # No repo policy - use global defaults
            global_policy = self.get_global_policy()
            return PolicyEngine(global_policy.to_compiled(GlobalPolicy()))

        global_policy = self.get_global_policy()
        return PolicyEngine(repo_policy.to_compiled(global_policy))

    def simulate(self, repo_root: str, candidate: dict[str, Any]) -> dict[str, Any]:
        """Simulate a candidate policy."""
        # Load current policy
        current = self.get_repo_policy(repo_root)
        global_policy = self.get_global_policy()

        # Compile both
        current_compiled = current.to_compiled(global_policy) if current else None
        candidate_policy = RepoPolicy.from_yaml(candidate)
        candidate_compiled = candidate_policy.to_compiled(global_policy)

        # Test paths against both
        # This is a simplified version - real implementation would test more paths
        return {
            "current": "compiled" if current_compiled else "none",
            "candidate": "compiled",
            "diff": "simulated",
        }

    def diff(self, repo_root: str, candidate: dict[str, Any]) -> dict[str, Any]:
        """Diff current vs candidate policy."""
        current = self.get_repo_policy(repo_root)
        return {
            "has_current": current is not None,
            "candidate_keys": list(candidate.keys()),
        }