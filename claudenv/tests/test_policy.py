"""
Tests for claudenv domain policy - minimal working tests.
"""
from __future__ import annotations

import pytest
import re
import tempfile
from pathlib import Path

from claudenv.domain.policy_rules import (
    GlobPathRule, RegexPathRule, ExtensionMatchRule, SecretContentRule,
    RuleFactory,
)


class TestGlobPathRule:
    def test_match_simple(self):
        rule = GlobPathRule("*.py")
        assert rule.matches("test.py")
        # * doesn't match / - use **/*.py for nested
        assert not rule.matches("src/main.py")

    def test_describe(self):
        rule = GlobPathRule("*.py")
        assert rule.describe() == "glob:*.py"


class TestRegexPathRule:
    def test_match(self):
        rule = RegexPathRule(r"src/.*\.py$")
        assert rule.matches("src/main.py")
        assert rule.matches("src/utils/helper.py")
        assert not rule.matches("src/main.pyc")
        assert not rule.matches("test/main.py")

    def test_invalid_regex(self):
        # Invalid regex raises re.error
        with pytest.raises(re.error):
            RegexPathRule(r"[invalid")


class TestExtensionMatchRule:
    def test_match(self):
        rule = ExtensionMatchRule(".py")
        assert rule.matches("test.py")
        assert rule.matches("src/main.py")
        assert not rule.matches("test.txt")

    def test_extension_property(self):
        rule = ExtensionMatchRule(".py")
        assert rule.extension == ".py"


class TestSecretContentRule:
    def test_detect_aws_key(self):
        rule = SecretContentRule("aws_key", r"AKIA[0-9A-Z]{16}")
        content = "aws_key = 'AKIAIOSFODNN7EXAMPLE'"
        hits = rule.findall(content)
        assert len(hits) > 0

    def test_sub(self):
        rule = SecretContentRule("test", r"secret\d+")
        text = "password = secret123"
        result = rule.sub(text, "[REDACTED]")
        assert "[REDACTED]" in result


class TestRuleFactory:
    def test_create_path_rule_glob(self):
        rule = RuleFactory.create_path_rule("*.py")
        assert isinstance(rule, GlobPathRule)

    def test_create_extension_rule(self):
        rule = RuleFactory.create_extension_rule(".py")
        assert isinstance(rule, ExtensionMatchRule)

    def test_create_content_rule(self):
        rule = RuleFactory.create_content_rule("secret", r"secret\d+")
        assert isinstance(rule, SecretContentRule)
