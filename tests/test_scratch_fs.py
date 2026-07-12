"""Coverage for the scratch:// path scheme in
mcp-servers/filesystem-policy/server.py. Run: pytest tests/ -q

Regression context: agents need a place to do disposable work (intermediate
files, downloaded artifacts) without every read/write being subject to the
full repo policy allow-list -- but the deny rules (secrets, .ssh, .env, etc.)
must still protect anything scratch commands touch OUTSIDE the scratch
directory. Scoped per-repo (CLAUDE_ENV_REPO_NAME), not per-session --
CLAUDE_ENV_SESSION is not wired to a shared value across MCP server
processes today (see docs/superpowers/specs/2026-07-12-secure-scratchpad-design.md
decision 1).
"""
import importlib.util
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


def _load_server(monkeypatch, repo_root: Path, home: Path):
    """Import mcp-servers/filesystem-policy/server.py against a fresh fake
    repo root and CLAUDE_ENV_HOME, with the mcp package stubbed out (this
    test only exercises path resolution, not the stdio protocol).

    Uses monkeypatch (not a bare os.environ[...] = ...) so these env vars
    are restored after each test -- a previous version of this helper set
    them directly and left CLAUDE_ENV_REPO_ROOT pointing at a deleted
    tmp_path dir for the rest of the pytest session, which broke an
    unrelated terminal-server test that reads the same env var at module
    load (test_terminal_command_chaining.py collects alphabetically after
    this file)."""
    monkeypatch.setenv("CLAUDE_ENV_REPO_ROOT", str(repo_root))
    monkeypatch.setenv("CLAUDE_ENV_HOME", str(home))
    monkeypatch.setenv("CLAUDE_ENV_GLOBAL_POLICY", str(_ROOT / "config" / "global-policy.yaml"))

    sys.modules["mcp"] = types.ModuleType("mcp")
    mod_mcp_server = types.ModuleType("mcp.server")
    mod_mcp_server.Server = lambda name: types.SimpleNamespace(
        list_tools=lambda: (lambda f: f), call_tool=lambda: (lambda f: f))
    sys.modules["mcp.server"] = mod_mcp_server
    mod_mcp_stdio = types.ModuleType("mcp.server.stdio")
    mod_mcp_stdio.stdio_server = None
    sys.modules["mcp.server.stdio"] = mod_mcp_stdio
    mod_mcp_types = types.ModuleType("mcp.types")
    mod_mcp_types.TextContent = None
    mod_mcp_types.Tool = None
    sys.modules["mcp.types"] = mod_mcp_types

    spec = importlib.util.spec_from_file_location(
        "_fs_policy_server", _ROOT / "mcp-servers" / "filesystem-policy" / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "demo-repo"
    (repo / ".claude").mkdir(parents=True)
    (repo / ".claude" / "repo-policy.yaml").write_text(
        (_ROOT / "config" / "repo-policy.template.yaml").read_text())
    return repo


def test_scratch_path_resolves_under_claude_env_home_scratch(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    home = tmp_path / "home"
    srv = _load_server(monkeypatch, repo, home)

    rel, abs_path = srv._resolve("scratch://notes.txt")
    assert abs_path == home / "scratch" / "demo-repo" / "notes.txt"


def test_scratch_path_escape_is_rejected(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    home = tmp_path / "home"
    srv = _load_server(monkeypatch, repo, home)

    import pytest
    with pytest.raises(srv.PolicyBlocked):
        srv._resolve("scratch://../../etc/passwd")


def test_ordinary_repo_path_unaffected_by_scratch_scheme(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    home = tmp_path / "home"
    srv = _load_server(monkeypatch, repo, home)

    rel, abs_path = srv._resolve("src/app.py")
    assert abs_path == repo / "src" / "app.py"


def test_scratch_dir_is_wiped_and_recreated_on_start(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    home = tmp_path / "home"
    scratch_dir = home / "scratch" / "demo-repo"
    scratch_dir.mkdir(parents=True)
    (scratch_dir / "stale.txt").write_text("leftover from a previous session\n")

    srv = _load_server(monkeypatch, repo, home)
    srv._reset_scratch_dir()

    assert scratch_dir.is_dir()
    assert not (scratch_dir / "stale.txt").exists()
