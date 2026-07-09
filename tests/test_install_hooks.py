"""Coverage for the repo-local hook installer (hooks/install_hooks.py).
Run: pytest tests/ -q

These exercise the installer's target resolution, portable command emission,
merge safety, idempotency and legacy-aware uninstall directly — no Claude Code
subprocess needed. The installer imports only stdlib, so we load it in-process
and drive main() by patching sys.argv.
"""
import importlib.util
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("install_hooks",
                                               _ROOT / "hooks" / "install_hooks.py")
ih = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ih)


def _run(argv, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["install_hooks.py", *argv])
    return ih.main()


def _hooks(settings_path: Path) -> dict:
    return json.loads(settings_path.read_text())["hooks"]


def _all_commands(hooks: dict) -> list[str]:
    cmds = []
    for event in ("PreToolUse", "PostToolUse"):
        for entry in hooks.get(event, []):
            for h in entry.get("hooks", []):
                cmds.append(h.get("command", ""))
    return cmds


# -- target resolution -------------------------------------------------------

def test_default_target_is_repo_local(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _run([], monkeypatch) == 0
    settings = tmp_path / ".claude" / "settings.json"
    assert settings.exists(), "default install must land in <cwd>/.claude/settings.json"


def test_repo_flag_targets_that_repo(tmp_path, monkeypatch):
    repo = tmp_path / "myrepo"
    assert _run(["--repo", str(repo)], monkeypatch) == 0
    assert (repo / ".claude" / "settings.json").exists()


def test_global_flag_targets_home(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(ih, "GLOBAL_SETTINGS", fake_home / ".claude" / "settings.json")
    assert _run(["--global"], monkeypatch) == 0
    assert (fake_home / ".claude" / "settings.json").exists()


# -- portable command --------------------------------------------------------

def test_command_uses_env_var_not_absolute_path(tmp_path, monkeypatch):
    assert _run(["--repo", str(tmp_path)], monkeypatch) == 0
    cmds = _all_commands(_hooks(tmp_path / ".claude" / "settings.json"))
    assert cmds, "expected hook commands to be written"
    for c in cmds:
        assert "$CLAUDE_ENV_HOME" in c, f"command not portable: {c}"
        assert "/Users/" not in c and str(Path.home()) not in c, \
            f"command baked in an absolute path: {c}"
    assert any("policy_hook.py" in c for c in cmds)
    assert any("audit_hook.py" in c for c in cmds)


# -- idempotency + merge safety ---------------------------------------------

def test_reinstall_is_idempotent(tmp_path, monkeypatch):
    _run(["--repo", str(tmp_path)], monkeypatch)
    _run(["--repo", str(tmp_path)], monkeypatch)
    cmds = _all_commands(_hooks(tmp_path / ".claude" / "settings.json"))
    # exactly one policy + one audit command, no duplicates
    assert sum("policy_hook.py" in c for c in cmds) == 1
    assert sum("audit_hook.py" in c for c in cmds) == 1


def test_merge_preserves_existing_settings(tmp_path, monkeypatch):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({
        "model": "opusmax",
        "hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}
        ]},
    }))
    assert _run(["--repo", str(tmp_path)], monkeypatch) == 0
    data = json.loads(settings.read_text())
    assert data["model"] == "opusmax"                      # unrelated key survived
    cmds = _all_commands(data["hooks"])
    assert "echo mine" in cmds                             # foreign hook survived
    assert any("policy_hook.py" in c for c in cmds)        # ours added


# -- uninstall (incl. legacy absolute-path installs) -------------------------

def test_uninstall_removes_portable_hooks(tmp_path, monkeypatch):
    _run(["--repo", str(tmp_path)], monkeypatch)
    _run(["--repo", str(tmp_path), "--uninstall"], monkeypatch)
    cmds = _all_commands(_hooks(tmp_path / ".claude" / "settings.json"))
    assert not any("policy_hook.py" in c or "audit_hook.py" in c for c in cmds)


def test_uninstall_removes_legacy_absolute_hooks(tmp_path, monkeypatch):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    legacy_pre = "/Users/someone/.claude-env/venv/bin/python /Users/someone/.claude-env/hooks/policy_hook.py"
    legacy_post = "/Users/someone/.claude-env/venv/bin/python /Users/someone/.claude-env/hooks/audit_hook.py"
    settings.write_text(json.dumps({"hooks": {
        "PreToolUse": [{"matcher": "Read", "hooks": [{"type": "command", "command": legacy_pre}]}],
        "PostToolUse": [{"matcher": "Write", "hooks": [{"type": "command", "command": legacy_post}]}],
    }}))
    assert _run(["--repo", str(tmp_path), "--uninstall"], monkeypatch) == 0
    cmds = _all_commands(_hooks(settings))
    assert not any("policy_hook.py" in c or "audit_hook.py" in c for c in cmds)


def test_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    assert _run(["--repo", str(tmp_path), "--dry-run"], monkeypatch) == 0
    assert not (tmp_path / ".claude" / "settings.json").exists()
    out = capsys.readouterr().out
    assert "$CLAUDE_ENV_HOME" in out                       # printed the portable command
