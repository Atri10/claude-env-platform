"""
Tests for claudenv domain value objects.
"""
from __future__ import annotations

import pytest
import re

from claudenv.domain.value_objects import (
    RepoSlug, Tier, ChunkId, EventId, RequestId, SessionId,
    NodeId, EdgeId, BranchName, ContentHash, TableName,
    utc_now, iso_now, GlobPattern, RegexRule,
    ExtensionRule, ContentPattern, PolicyRuleSet, ContentScanConfig,
)


class TestRepoSlug:
    def test_generate(self):
        slug = RepoSlug.generate("my-repo")
        assert str(slug) == "my-repo"

    def test_from_string(self):
        slug = RepoSlug.from_string("owner/repo")
        assert str(slug) == "owner/repo"

    def test_generate_sanitizes(self):
        slug = RepoSlug.generate("My Repo!")
        assert str(slug) == "my-repo"


class TestTier:
    def test_values(self):
        assert Tier.PUBLIC.value == 0
        assert Tier.INTERNAL.value == 1
        assert Tier.SENSITIVE.value == 2
        assert Tier.RESTRICTED.value == 3

    def test_labels(self):
        assert Tier.PUBLIC.label == "public"
        assert Tier.INTERNAL.label == "internal"
        assert Tier.SENSITIVE.label == "sensitive"
        assert Tier.RESTRICTED.label == "restricted"


class TestChunkId:
    def test_from_parts(self):
        cid = ChunkId.from_parts("repo", "path.py", 10, 20)
        assert len(str(cid)) == 40  # SHA1 hex

    def test_from_string(self):
        cid = ChunkId.from_string("abc123")
        assert str(cid) == "abc123"


class TestEventId:
    def test_generate(self):
        id1 = EventId.generate()
        id2 = EventId.generate()
        assert str(id1) != str(id2)


class TestRequestId:
    def test_generate(self):
        id1 = RequestId.generate()
        id2 = RequestId.generate()
        assert str(id1) != str(id2)


class TestSessionId:
    def test_from_string(self):
        sid = SessionId.from_string("test-session")
        assert str(sid) == "test-session"

    def test_generate(self):
        sid1 = SessionId.generate()
        sid2 = SessionId.generate()
        assert str(sid1) != str(sid2)


class TestNodeId:
    def test_generate(self):
        id1 = NodeId.generate()
        id2 = NodeId.generate()
        assert str(id1) != str(id2)


class TestEdgeId:
    def test_generate(self):
        id1 = EdgeId.generate()
        id2 = EdgeId.generate()
        assert str(id1) != str(id2)


class TestBranchName:
    def test_from_string(self):
        branch = BranchName.from_string("main")
        assert str(branch) == "main"

    def test_default(self):
        branch = BranchName.default()
        assert str(branch) == "main"


class TestContentHash:
    def test_from_string(self):
        h = ContentHash.from_string("abc123")
        assert str(h) == "abc123"

    def test_compute(self):
        h = ContentHash.compute(b"test content")
        assert len(str(h)) == 64  # SHA256 hex


class TestTableName:
    def test_generate(self):
        name = TableName.generate("my-repo", "main")
        assert "my-repo" in str(name)


class TestUtcNow:
    def test_returns_datetime(self):
        now = utc_now()
        assert now is not None

    def test_iso_now_format(self):
        iso = iso_now()
        assert "T" in iso
        assert iso.endswith("Z")


class TestGlobPattern:
    def test_match_simple(self):
        pattern = GlobPattern("*.py")
        assert pattern.matches("test.py")
        # * does NOT match / - use **/*.py for nested
        assert not pattern.matches("src/main.py")

    def test_match_nested(self):
        pattern = GlobPattern("src/**/*.py")
        assert pattern.matches("src/main.py")
        assert pattern.matches("src/utils/helper.py")
        assert not pattern.matches("test.py")

    def test_match_recursive(self):
        pattern = GlobPattern("**/*.py")
        assert pattern.matches("test.py")
        assert pattern.matches("src/main.py")
        assert pattern.matches("a/b/c/test.py")


class TestRegexRule:
    def test_match(self):
        rule = RegexRule.create(r"src/.*\.py$", "python_src")
        assert rule.matches("src/main.py")
        assert rule.matches("src/utils/helper.py")
        assert not rule.matches("src/main.pyc")
        assert not rule.matches("test/main.py")

    def test_invalid_regex(self):
        with pytest.raises(re.error):
            RegexRule.create(r"[invalid", "test")


class TestExtensionRule:
    def test_match(self):
        rule = ExtensionRule(".py")
        assert rule.matches("test.py")
        assert rule.matches("src/main.py")
        assert not rule.matches("test.txt")


class TestContentPattern:
    def test_create(self):
        pattern = ContentPattern.create("aws_key", r"AKIA[0-9A-Z]{16}")
        assert pattern.name == "aws_key"
        hits = pattern.findall("key = 'AKIAIOSFODNN7EXAMPLE'")
        assert len(hits) > 0

    def test_sub(self):
        pattern = ContentPattern.create("secret", r"secret\d+")
        text = "password = secret123"
        redacted = pattern.sub(text, "[REDACTED]")
        assert "[REDACTED]" in redacted


class TestPolicyRuleSet:
    def test_defaults(self):
        rules = PolicyRuleSet()
        assert rules.deny_paths == []
        assert rules.default_deny is False

    def test_with_rules(self):
        rules = PolicyRuleSet(
            deny_paths=[GlobPattern("*.txt")],
            deny_extensions=[ExtensionRule(".log")],
            default_deny=True,
        )
        assert len(rules.deny_paths) == 1
        assert len(rules.deny_extensions) == 1
        assert rules.default_deny is True


class TestContentScanConfig:
    def test_defaults(self):
        config = ContentScanConfig()
        assert config.enabled is True
        assert config.on_match == "redact"
        assert config.patterns == []

    def test_with_patterns(self):
        pattern = ContentPattern.create("test", r"secret")
        config = ContentScanConfig(
            enabled=True,
            on_match="block",
            patterns=[pattern],
        )
        assert config.enabled is True
        assert config.on_match == "block"
        assert len(config.patterns) == 1
