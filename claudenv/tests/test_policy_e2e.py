"""
End-to-end tests for ``claude-env policy_sim simulate``.

The command dry-runs a candidate repo-policy and reports the diff (newly
blocked / newly allowed / changed rule) without writing anything. These tests
drive the real CLI command and assert the printed diff, complementing
``test_policy_sim.py`` which exercises the ``PolicyService`` directly.
"""
from __future__ import annotations

import pytest
from click.testing import CliRunner

from claudenv.cli import cli


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CLAUDE_ENV_HOME", str(home))
    r = tmp_path / "repo"
    r.mkdir()
    (r / "src").mkdir()
    (r / "src" / "main.py").write_text("x\n")
    (r / "src" / "secret.py").write_text("y\n")
    (r / "docs").mkdir()
    (r / "docs" / "readme.md").write_text("z\n")
    return r
def _run(*args):
    return CliRunner().invoke(cli, list(args), catch_exceptions=False)


class TestPolicySimSimulateCLI:
    def test_reports_newly_blocked(self, repo, tmp_path):
        candidate = tmp_path / "candidate.yaml"
        candidate.write_text(
            "version: 1\nrepo: repo\ntier: 1\ndeny:\n  paths:\n    - \"src/secret.py\"\n"
        )
        result = _run("policy_sim", "simulate", str(repo), "--candidate", str(candidate))
        result = _run("policy-sim", "simulate", str(repo), "--candidate", str(candidate))
        assert "NEWLY BLOCKED" in result.output
        assert "src/secret.py" in result.output
        # Nothing was written to the repo's policy file.
        assert not (repo / ".claude" / "repo-policy.yaml").exists()

    def test_reports_newly_allowed(self, repo, tmp_path):
        # Current policy blocks src/secret.py; candidate lifts it.
        (repo / ".claude").mkdir(exist_ok=True)
        (repo / ".claude" / "repo-policy.yaml").write_text(
            "version: 1\nrepo: repo\ntier: 1\ndeny:\n  paths:\n    - \"src/secret.py\"\n"
        )
        candidate = tmp_path / "candidate.yaml"
        candidate.write_text("version: 1\nrepo: repo\ntier: 1\n")
        result = _run("policy-sim", "simulate", str(repo), "--candidate", str(candidate))
        assert result.exit_code == 0
        assert "NEWLY ALLOWED" in result.output
        assert "src/secret.py" in result.output

    def test_no_change_when_candidate_matches(self, repo, tmp_path):
        (repo / ".claude").mkdir(exist_ok=True)
        (repo / ".claude" / "repo-policy.yaml").write_text(
            "version: 1\nrepo: repo\ntier: 1\ndeny:\n  paths:\n    - \"src/secret.py\"\n"
        )
        candidate = tmp_path / "candidate.yaml"
        candidate.write_text(
            "version: 1\nrepo: repo\ntier: 1\ndeny:\n  paths:\n    - \"src/secret.py\"\n"
        )
        result = _run("policy-sim", "simulate", str(repo), "--candidate", str(candidate))
        assert result.exit_code == 0
        assert "NEWLY BLOCKED" not in result.output
        assert "NEWLY ALLOWED" not in result.output
