"""
Tests for ``PolicyService.simulate`` -- a real dry-run comparison of the
current vs a candidate repo policy over a repo's files.

Uses plain directories (no git) so the test does not depend on the
developer's git configuration; ``PolicyService.simulate`` enumerates files via
rglob when git is unavailable, which is exactly the fallback path exercised
here.
"""
from __future__ import annotations

from pathlib import Path

from claudenv.adapters.config import get_config
from claudenv.domain.policy import PolicyService


def _write_tree(repo: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


class TestPolicySimulation:
    def test_newly_blocked_reported(self, tmp_path):
        repo = tmp_path / "repo"
        _write_tree(repo, {
            "src/main.py": "x\n",
            "src/secret.py": "y\n",
            "docs/readme.md": "z\n",
        })
        # Candidate denies src/secret.py (currently allowed: no repo policy).
        candidate = {
            "version": 1, "tier": 1, "repo": "repo",
            "deny": {"paths": ["src/secret.py"]},
        }
        result = PolicyService(get_config()).simulate(str(repo), candidate)

        assert result["files"] == 3
        blocked_paths = {f for f, _, _ in result["newly_blocked"]}
        assert blocked_paths == {"src/secret.py"}
        assert result["newly_allowed"] == []
        assert result["changed_rule"] == []
        # Exact counts hold here because no .claude/ policy file is present.
        assert result["blocked_current"] == 0
        assert result["blocked_candidate"] == 1

    def test_newly_allowed_reported(self, tmp_path):
        repo = tmp_path / "repo"
        _write_tree(repo, {"src/main.py": "x\n", "src/secret.py": "y\n"})
        # Current repo policy blocks src/secret.py.
        _write_tree(repo, {".claude/repo-policy.yaml":
            "version: 1\nrepo: repo\ntier: 1\ndeny:\n  paths:\n    - \"src/secret.py\"\n"})
        # Candidate lifts the deny -> src/secret.py becomes allowed.
        candidate = {"version": 1, "tier": 1, "repo": "repo"}
        result = PolicyService(get_config()).simulate(str(repo), candidate)

        allowed_paths = {f for f, _, _ in result["newly_allowed"]}
        assert allowed_paths == {"src/secret.py"}
        assert result["newly_blocked"] == []
        assert result["changed_rule"] == []
        # blocked_candidate == blocked_current + newly_blocked - newly_allowed
        assert result["blocked_candidate"] == (
            result["blocked_current"] + len(result["newly_blocked"])
            - len(result["newly_allowed"])
        )

    def test_no_change_when_candidate_matches_current(self, tmp_path):
        repo = tmp_path / "repo"
        _write_tree(repo, {"src/main.py": "x\n", "src/secret.py": "y\n"})
        _write_tree(repo, {".claude/repo-policy.yaml":
            "version: 1\nrepo: repo\ntier: 1\ndeny:\n  paths:\n    - \"src/secret.py\"\n"})
        # Same policy as candidate -> nothing changes.
        candidate = {
            "version": 1, "tier": 1, "repo": "repo",
            "deny": {"paths": ["src/secret.py"]},
        }
        result = PolicyService(get_config()).simulate(str(repo), candidate)

        assert result["newly_blocked"] == []
        assert result["newly_allowed"] == []
        assert result["changed_rule"] == []
        assert result["blocked_current"] == result["blocked_candidate"]
