"""
End-to-end onboarding + model-setup + index/rag test.

Drives the REAL CLI commands (`onboard`, `model setup`, `index`, `rag`) through
click's CliRunner against an isolated temp $CLAUDE_ENV_HOME + SQLite DB and a
real git repo, asserting observable behavior:

  * onboard writes .claude/repo-policy.yaml including the detected `default_branch`
  * `model setup -y` writes the deployed rag.yaml (embedding + reranker keys)
  * `index` builds a real RAG index (chunks > 0)
  * `rag` retrieval returns real chunks
  * GitHooksStep installs real, executable reindex hooks

The dummy embedder backend (EMBED_BACKEND=dummy) lets the RAG stack run without
a real model file.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

import claudenv.di as di
from claudenv._data import sql_dir
from claudenv.cli import cli


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
        env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    )


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Isolated CLI: temp home, schema-applied DB, dummy embedder, fake ~."""
    home = tmp_path / "home"
    (home / "state").mkdir(parents=True)
    (home / "config").mkdir(parents=True)
    dsn = f"sqlite:///{home}/state/claude-env.db"

    monkeypatch.setenv("CLAUDE_ENV_HOME", str(home))
    monkeypatch.setenv("CLAUDE_ENV_DSN", dsn)
    monkeypatch.setenv("EMBED_BACKEND", "dummy")
    monkeypatch.setenv("RERANKER_BACKEND", "noop")
    # Keep MCPEnvStep from touching the developer's real ~/.claude.json.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "fakehome"))

    from claudenv.adapters.persistence import SQLiteDatabase
    db = SQLiteDatabase(dsn)
    for f in sorted(sql_dir().glob("*.sql")):
        db._conn.executescript(f.read_text())
    db.close()

    di.reset_container()
    yield home
    di.reset_container()


@pytest.fixture()
def repo(tmp_path) -> Path:
    """A small, committed git repo to onboard + index."""
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    (r / "auth.py").write_text(
        "def authenticate_user(token):\n    return verify(token)\n"
    )
    (r / "readme.md").write_text("# Service\nThis component handles login.\n")
    (r / "main.py").write_text("def main():\n    print('hi')\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "init")
    return r


def _run(*args, input=None):
    return CliRunner().invoke(cli, list(args), input=input, catch_exceptions=False)


class TestOnboardingE2E:
    def test_onboard_writes_policy_with_default_branch_and_hooks(self, env, repo):
        # Interactive onboard: 5 prompts (slug, tier, desc, branch, confirm)
        # all accept the detected default via empty input.
        result = _run("onboard", str(repo), "-i", input="\n\n\n\n\n")
        assert result.exit_code == 0, result.output

        policy = repo / ".claude" / "repo-policy.yaml"
        assert policy.exists()
        text = policy.read_text()
        assert 'repo: "repo"' in text
        # The detected branch (whatever git init chose) is recorded.
        assert "default_branch:" in text

        # GitHooksStep must install real, executable hooks + the reindex
        # entrypoint into .git/hooks.
        hooks = repo / ".git" / "hooks"
        for name in ("post-commit", "post-merge", "post-checkout", "claude-env-reindex"):
            p = hooks / name
            assert p.exists(), f"missing hook: {name}"
            assert os.access(p, os.X_OK), f"hook not executable: {name}"
        assert "claude-env-reindex" in (hooks / "post-commit").read_text()

        # Executing the hook must not error (it no-ops if claude-env is absent
        # or the repo isn't onboarded for indexing).
        rc = subprocess.run(
            ["sh", str(hooks / "post-commit")],
            cwd=str(repo), capture_output=True, text=True,
        ).returncode
        assert rc == 0, rc

    def test_model_setup_writes_rag_yaml(self, env, repo):
        result = _run("model", "setup", "-y")
        assert result.exit_code == 0, result.output
        assert "Wrote RAG config" in result.output

        rag = Path(env) / "config" / "rag.yaml"
        assert rag.exists()
        import yaml
        data = yaml.safe_load(rag.read_text())
        assert "embedding" in data and "reranker" in data
        # Dummy backend + 768 dim survive the round-trip.
        assert data["embedding"]["embedding_dim"] == 768
        assert data["embedding"]["pooling_type"] == "mean"

    def test_model_setup_accepts_flags(self, env, repo):
        # CI path: explicit flags, no TTY, no prompts.
        result = _run(
            "model", "setup", "-y",
            "--embedding-dim", "512",
            "--pooling-type", "cls",
            "--model-path", "",
        )
        assert result.exit_code == 0, result.output
        import yaml
        data = yaml.safe_load((Path(env) / "config" / "rag.yaml").read_text())
        assert data["embedding"]["embedding_dim"] == 512
        assert data["embedding"]["pooling_type"] == "cls"

    def test_init_interactive_writes_rag_yaml(self, env, repo):
        # `init --interactive` must run model setup (non-interactively under a
        # non-TTY) and write the deployed rag.yaml without hanging.
        result = _run("init", "--interactive")
        assert result.exit_code == 0, result.output
        assert (Path(env) / "config" / "rag.yaml").exists()

    def test_full_pipeline_index_then_rag(self, env, repo):
        # onboard -> model setup -> index -> rag, asserting real behavior.
        assert _run("onboard", str(repo), "-y").exit_code == 0
        assert _run("model", "setup", "-y").exit_code == 0

        idx = _run("index", str(repo))
        assert idx.exit_code == 0, idx.output
        assert "Indexed:" in idx.output
        # Real index: all three committed files produced chunks.
        assert "Indexed: 3 files" in idx.output

        rag = _run("rag", str(repo), "authenticate")
        assert rag.exit_code == 0, rag.output
        # Retrieval returns real chunks (the query term appears in output).
        assert "authenticate" in rag.output

    def test_index_respects_branch_override(self, env, repo):
        assert _run("onboard", str(repo), "-y").exit_code == 0
        assert _run("model", "setup", "-y").exit_code == 0

        # Force a branch that won't match the detected one; index must still
        # build using the override rather than hardcoding "main".
        idx = _run("index", str(repo), "--branch", "feature-x")
        assert idx.exit_code == 0, idx.output
        assert "branch=feature-x" in idx.output


class TestEnsureInitialized:
    def test_index_without_init_prompts_and_aborts(self, env, repo):
        # Remove the DB so the home looks uninitialized (config dir stays).
        (Path(env) / "state" / "claude-env.db").unlink()

        # Answer "n" to the init prompt -> actionable error, no raw traceback.
        result = _run("index", str(repo), input="n\n")
        assert result.exit_code != 0
        assert "not initialized" in result.output
        # DB was NOT created when the user declined.
        assert not (Path(env) / "state" / "claude-env.db").exists()

    def test_index_without_init_auto_inits_on_yes(self, env, repo):
        (Path(env) / "state" / "claude-env.db").unlink()

        # Answer "y" -> runs init (DB created), then proceeds to the
        # onboarding check (repo not yet onboarded -> clear error, not a trace).
        result = _run("index", str(repo), input="y\n")
        # DB now exists because init ran.
        assert (Path(env) / "state" / "claude-env.db").exists()
        # The command then fails with the onboarding message, not a stack trace.
        assert "not onboarded" in result.output
