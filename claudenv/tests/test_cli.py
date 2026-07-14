"""
CLI smoke tests — invoke every registered command through click's CliRunner
against an isolated temp $CLAUDE_ENV_HOME + SQLite DB, asserting real exit codes
and output.

These exist because the suite previously had zero CLI coverage, which let a
broken `bootstrap` command (calling a nonexistent run_bootstrap) ship unnoticed.
Each test drives the actual command callback and the DI container, so an
import/wiring break surfaces here instead of at runtime.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

import claudenv.di as di
from claudenv.cli import cli

_SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Isolate the CLI: temp home, temp DB with schema applied, dummy embedder."""
    home = tmp_path / "home"
    (home / "state").mkdir(parents=True)
    (home / "config").mkdir(parents=True)
    dsn = f"sqlite:///{home}/state/claude-env.db"

    monkeypatch.setenv("CLAUDE_ENV_HOME", str(home))
    monkeypatch.setenv("CLAUDE_ENV_DSN", dsn)
    monkeypatch.setenv("EMBED_BACKEND", "dummy")
    monkeypatch.setenv("RERANKER_BACKEND", "noop")

    # Apply the platform schema so DB-backed commands have their tables.
    from claudenv.adapters.persistence import SQLiteDatabase
    db = SQLiteDatabase(dsn)
    for f in sorted(_SQL_DIR.glob("*.sql")):
        db._conn.executescript(f.read_text())
    db.close()

    # The container is a global singleton keyed off env at construction time.
    di.reset_container()
    yield home
    di.reset_container()


def _run(*args):
    return CliRunner().invoke(cli, list(args), catch_exceptions=False)


class TestCliWiring:
    def test_help_lists_expected_commands(self):
        result = _run("--help")
        assert result.exit_code == 0
        for cmd in ("budget", "dashboard", "feedback", "report", "replay",
                    "incident", "services", "scan", "index", "rag", "onboard",
                    "hooks", "validate"):
            assert cmd in result.output

    def test_bootstrap_command_is_gone(self):
        """Regression: the broken bootstrap command was removed, not resurrected."""
        assert "bootstrap" not in cli.commands
        result = CliRunner().invoke(cli, ["bootstrap"], catch_exceptions=False)
        assert result.exit_code != 0  # click reports "No such command"


class TestObservabilityCommands:
    def test_budget_empty(self, env):
        result = _run("budget")
        assert result.exit_code == 0  # no spend -> not exceeded
        assert "No session cost data yet." in result.output

    def test_budget_json(self, env):
        result = _run("budget", "--format", "json")
        assert result.exit_code == 0
        import json
        payload = json.loads(result.output)
        assert payload["overall"] in ("ok", "warning", "EXCEEDED", "unlimited")

    def test_budget_exceeded_exits_nonzero(self, env):
        from claudenv.adapters.persistence import SQLiteDatabase
        db = SQLiteDatabase(f"sqlite:///{env}/state/claude-env.db")
        db.execute(
            "INSERT INTO metrics_sessions(session_id,repo,started_at,input_tokens,output_tokens,est_cost_usd) "
            "VALUES (?,?,?,?,?,?)",
            ("s1", "payments", "2999-01-15T00:00:00", 10, 5, 500.0),
        )
        db.close()
        (env / "config" / "budgets.yaml").write_text(
            "warn_at: 0.8\nmonthly_usd:\n  default: 0\n  repos:\n    payments: 100\n"
        )
        di.reset_container()
        result = CliRunner().invoke(cli, ["budget"], catch_exceptions=False)
        assert result.exit_code == 1
        assert "EXCEEDED" in result.output

    def test_dashboard_runs(self, env):
        result = _run("dashboard", "--window", "7d")
        assert result.exit_code == 0
        assert "dashboard" in result.output.lower()

    def test_feedback_runs(self, env):
        result = _run("feedback")
        assert result.exit_code == 0
        assert "retrieved:" in result.output


class TestOpsCommands:
    def test_incident_lifecycle(self, env):
        assert "OFF" in _run("incident", "status").output
        assert "ON" in _run("incident", "on", "--reason", "drill").output
        assert "ACTIVE" in _run("incident", "status").output
        assert "OFF" in _run("incident", "off").output

    def test_services_empty(self, env):
        result = _run("services")
        assert result.exit_code == 0
        assert "No services running" in result.output

    def test_report_runs(self, env):
        result = _run("report", "--window", "7d")
        assert result.exit_code == 0

    def test_replay_list(self, env):
        result = _run("replay", "--list")
        assert result.exit_code == 0
