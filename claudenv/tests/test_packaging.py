"""
Tests for pip-installable packaging: packaged data resolution, the `init`
command (idempotent home provisioning + genesis audit event), and the MCP
launch configuration pointing at importable modules.
"""
from __future__ import annotations

import importlib
import json

import pytest
from click.testing import CliRunner

import claudenv.di as di
from claudenv._data import config_dir, sql_dir, templates_dir
from claudenv.cli import cli

# --------------------------------------------------------------------------
# Packaged data resolution
# --------------------------------------------------------------------------

class TestPackagedData:
    def test_config_dir_has_all_defaults(self):
        names = {p.name for p in config_dir().glob("*") if p.is_file()}
        assert {
            "global-policy.yaml", "rag.yaml", "mcp-servers.json",
            "budgets.yaml", "repo-policy.template.yaml",
        } <= names

    def test_sql_dir_has_schema(self):
        sql_files = sorted(p.name for p in sql_dir().glob("*.sql"))
        assert "001_schema.sql" in sql_files
        assert len(sql_files) >= 3

    def test_templates_dir_has_repo_onboarding(self):
        onboarding = templates_dir() / "repo-onboarding"
        assert onboarding.is_dir()
        # the .claude dotdir tree must be present (agents + skills)
        assert (onboarding / ".claude" / "agents").is_dir()
        assert list((onboarding / ".claude" / "agents").glob("*.md"))


# --------------------------------------------------------------------------
# `claude-env init`
# --------------------------------------------------------------------------

@pytest.fixture()
def fresh_home(tmp_path, monkeypatch):
    """A clean, uninitialized $CLAUDE_ENV_HOME (no schema, no config)."""
    home = tmp_path / "home"
    dsn = f"sqlite:///{home}/state/claude-env.db"
    monkeypatch.setenv("CLAUDE_ENV_HOME", str(home))
    monkeypatch.setenv("CLAUDE_ENV_DSN", dsn)
    monkeypatch.setenv("EMBED_BACKEND", "dummy")
    monkeypatch.setenv("RERANKER_BACKEND", "noop")
    di.reset_container()
    yield home
    di.reset_container()


def _run(*args):
    return CliRunner().invoke(cli, list(args), catch_exceptions=False)


class TestInitCommand:
    def test_init_provisions_home(self, fresh_home):
        result = _run("init")
        assert result.exit_code == 0, result.output

        # config copied
        for name in ("global-policy.yaml", "rag.yaml", "mcp-servers.json",
                     "budgets.yaml", "repo-policy.template.yaml"):
            assert (fresh_home / "config" / name).exists()

        # DB created with schema; audit chain green
        assert (fresh_home / "state" / "claude-env.db").exists()
        assert "verify_chain: green" in result.output
        assert "ready" in result.output.lower()

    def test_init_is_idempotent(self, fresh_home):
        first = _run("init")
        assert first.exit_code == 0

        # Edit a config file; a plain re-run must NOT clobber it.
        rag = fresh_home / "config" / "rag.yaml"
        rag.write_text(rag.read_text() + "\n# user edit\n")

        second = _run("init")
        assert second.exit_code == 0
        assert "# user edit" in rag.read_text()  # preserved
        assert "verify_chain: green" in second.output  # chain still verifies

    def test_init_force_config_overwrites(self, fresh_home):
        _run("init")
        rag = fresh_home / "config" / "rag.yaml"
        rag.write_text("# clobber me\n")

        result = _run("init", "--force-config")
        assert result.exit_code == 0
        assert "# clobber me" not in rag.read_text()  # reset to packaged default


# --------------------------------------------------------------------------
# MCP launch configuration
# --------------------------------------------------------------------------

class TestMcpLaunchConfig:
    def test_all_servers_launch_importable_modules(self):
        doc = json.loads((config_dir() / "mcp-servers.json").read_text())
        for name, spec in doc["mcpServers"].items():
            if name == "jetbrains":  # IDE-provided, no python module
                continue
            args = spec["args"]
            assert args[0] == "-m", f"{name} should launch via -m, got {args}"
            module = args[1]
            # the module must be importable (and thus runnable via -m)
            importlib.import_module(module)
