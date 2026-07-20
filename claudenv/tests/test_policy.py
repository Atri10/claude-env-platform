"""
Tests for the live claude-env policy engine (``claudenv.domain.policy``).

These exercise the real evaluation path used in production:

    RepoPolicy.from_yaml + GlobalPolicy.from_yaml
        -> CompiledPolicy (via PolicyEngine.from_yaml)
        -> CompiledPolicy.evaluate / scan_content

The policy engine is pure (no I/O); every behaviour here is deterministic and
side-effect free, so it is safe to test directly without a database or model.
"""
from __future__ import annotations

from claudenv.domain.policy import PolicyEngine
from claudenv.domain.policy.results import PolicyDecision
from claudenv.domain.value_objects import Action

GLOBAL_ALLOW_ALL: dict = {
    "tier": 1,
    "deny": {"paths": [], "extensions": [], "regex": []},
    "allow": {"paths": [], "extensions": []},
    "tiers": {},
}

REPO_ALLOW_ALL: dict = {
    "version": 1,
    "tier": 1,
    "repo": "myrepo",
    "allow": {"paths": [], "extensions": []},
    "deny": {"paths": [], "extensions": [], "regex": []},
}


def _engine(global_data: dict, repo_data: dict) -> PolicyEngine:
    return PolicyEngine.from_yaml(global_data, repo_data)


class TestPathEvaluation:
    def test_default_allow_when_no_rules(self):
        engine = _engine(GLOBAL_ALLOW_ALL, REPO_ALLOW_ALL)
        decision = engine.evaluate_path("docs/readme.md")
        assert decision.is_allowed
        assert decision.action == Action.ALLOW

    def test_global_deny_path_blocks(self):
        g = {**GLOBAL_ALLOW_ALL, "deny": {"paths": ["secrets/**"]}}
        engine = _engine(g, REPO_ALLOW_ALL)
        decision = engine.evaluate_path("secrets/prod.env")
        assert not decision.is_allowed
        assert decision.action == Action.BLOCK
        assert "global" in decision.reason

    def test_repo_allow_coexists_with_global_deny(self):
        g = {**GLOBAL_ALLOW_ALL, "deny": {"paths": ["secrets/**"]}}
        r = {**REPO_ALLOW_ALL, "allow": {"paths": ["src/**"]}}
        engine = _engine(g, r)
        assert engine.evaluate_path("src/main.py").is_allowed
        assert not engine.evaluate_path("secrets/x").is_allowed

    def test_repo_deny_path_blocks(self):
        r = {**REPO_ALLOW_ALL, "deny": {"paths": ["**/dump_*.py"]}}
        engine = _engine(GLOBAL_ALLOW_ALL, r)
        decision = engine.evaluate_path("scripts/dump_db.py")
        assert not decision.is_allowed
        assert "repo" in decision.reason

    def test_repo_deny_regex_blocks(self):
        r = {
            **REPO_ALLOW_ALL,
            "deny": {
                "regex": [
                    {"pattern": ".*/forbidden_.*\\.py$", "reason": "forbidden module"}
                ]
            },
        }
        engine = _engine(GLOBAL_ALLOW_ALL, r)
        decision = engine.evaluate_path("a/forbidden_x.py")
        assert not decision.is_allowed
        assert "forbidden" in decision.reason

    def test_override_deny_beats_repo_deny(self):
        r = {
            **REPO_ALLOW_ALL,
            "deny": {"paths": ["src/**"]},
            "override_deny": ["src/allowed.py"],
        }
        engine = _engine(GLOBAL_ALLOW_ALL, r)
        # override_deny wins over the repo deny
        assert engine.evaluate_path("src/allowed.py").is_allowed
        # everything else under src/ is still denied
        assert not engine.evaluate_path("src/other.py").is_allowed


class TestTierDefaultDeny:
    def test_tier_default_deny_blocks_unlisted(self):
        g = {**GLOBAL_ALLOW_ALL, "tiers": {"3": {"default_deny": True}}}
        r = {**REPO_ALLOW_ALL, "tier": 3}
        engine = _engine(g, r)
        decision = engine.evaluate_path("anything/ungated.txt")
        assert not decision.is_allowed
        assert decision.rule == "default_deny"

    def test_tier_default_deny_still_allows_explicit(self):
        g = {**GLOBAL_ALLOW_ALL, "tiers": {"3": {"default_deny": True}}}
        r = {**REPO_ALLOW_ALL, "tier": 3, "allow": {"paths": ["src/**"]}}
        engine = _engine(g, r)
        assert engine.evaluate_path("src/main.py").is_allowed


class TestContentScan:
    @staticmethod
    def _scan_cfg(on_match: str = "redact") -> dict:
        return {
            "enabled": True,
            "on_match": on_match,
            "patterns": [{"name": "aws_key", "pattern": "AKIA[0-9A-Z]{16}"}],
        }

    def test_redact_replaces_secret(self):
        r = {**REPO_ALLOW_ALL, "content_scan": self._scan_cfg("redact")}
        engine = _engine(GLOBAL_ALLOW_ALL, r)
        redacted, hits = engine.scan_content("key=AKIAIOSFODNN7EXAMPLE end")
        assert "AKIAIOSFODNN7EXAMPLE" not in redacted
        assert "[REDACTED:aws_key]" in redacted
        assert hits == [("aws_key", 1)]

    def test_block_mode_returns_empty_with_sentinel(self):
        r = {**REPO_ALLOW_ALL, "content_scan": self._scan_cfg("block")}
        engine = _engine(GLOBAL_ALLOW_ALL, r)
        redacted, hits = engine.scan_content("key=AKIAIOSFODNN7EXAMPLE")
        assert redacted == ""
        assert hits == [("aws_key", -1)]

    def test_disabled_scan_is_noop(self):
        r = {
            **REPO_ALLOW_ALL,
            "content_scan": {"enabled": False, "patterns": []},
        }
        engine = _engine(GLOBAL_ALLOW_ALL, r)
        text = "key=AKIAIOSFODNN7EXAMPLE"
        out, hits = engine.scan_content(text)
        assert out == text
        assert hits == []


class TestPolicyDecision:
    def test_factories(self):
        assert PolicyDecision.allow().is_allowed
        assert not PolicyDecision.block("denied").is_allowed
        assert PolicyDecision.redact("x").action == Action.REDACT
