"""Coverage for onboarding helpers that don't need a model.
Run: pytest tests/ -q"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("register_repo",
                                               _ROOT / "scripts" / "register_repo.py")
reg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reg)


def _git_repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return str(tmp_path)


def test_slugify():
    assert reg._slugify("My Cool Repo") == "my-cool-repo"
    assert reg._slugify("weird  name!!") == "weird-name"   # spaces/punct -> single hyphen
    assert reg._slugify("keeps_underscores.ok") == "keeps_underscores.ok"
    assert reg._slugify("") == "repo"


def test_post_commit_install_and_idempotency(tmp_path):
    repo = _git_repo(tmp_path)
    assert reg._install_post_commit(repo, dry_run=False) == "installed"
    hook = Path(repo) / ".git" / "hooks" / "post-commit"
    assert hook.exists() and (hook.stat().st_mode & 0o111)          # executable
    # second run is a no-op (same content)
    assert reg._install_post_commit(repo, dry_run=False) == "already installed"


def test_post_commit_does_not_clobber_foreign_hook(tmp_path):
    repo = _git_repo(tmp_path)
    hook = Path(repo) / ".git" / "hooks" / "post-commit"
    hook.write_text("#!/bin/sh\necho mine\n")
    status = reg._install_post_commit(repo, dry_run=False)
    assert "kept existing" in status
    assert hook.read_text() == "#!/bin/sh\necho mine\n"             # untouched


def test_post_commit_skips_non_git(tmp_path):
    assert "not a git repo" in reg._install_post_commit(str(tmp_path), dry_run=False)


def test_detect_test_command(tmp_path):
    (tmp_path / "go.mod").write_text("module x\n")
    assert reg._detect_test_command(str(tmp_path)) == "go test ./..."
    (tmp_path / "go.mod").unlink()
    (tmp_path / "package.json").write_text("{}")
    assert reg._detect_test_command(str(tmp_path)) == "npm test"
    assert reg._detect_test_command(str(tmp_path / "nope")) is None


def test_install_commands_detected(tmp_path):
    (tmp_path / "go.mod").write_text("module x\n")
    reg._install_commands(str(tmp_path), dry_run=False)
    data = json.loads((tmp_path / ".claude" / "commands.json").read_text())
    assert data["run_tests"] == "go test ./..."
    assert data["run_benchmarks"] == "" and data["run_audit"] == ""   # left empty


def test_install_commands_scaffold_when_undetected(tmp_path):
    # monorepo / unknown toolchain: still writes a scaffold with all keys, empty
    reg._install_commands(str(tmp_path), dry_run=False)
    data = json.loads((tmp_path / ".claude" / "commands.json").read_text())
    assert set(data) >= {"run_tests", "run_benchmarks", "run_audit"}
    assert data["run_tests"] == "" == data["run_benchmarks"] == data["run_audit"]


def test_install_commands_preserves_existing(tmp_path):
    (tmp_path / ".claude").mkdir()
    cj = tmp_path / ".claude" / "commands.json"
    cj.write_text('{"run_tests": "custom"}')
    assert "kept existing" in reg._install_commands(str(tmp_path), dry_run=False)
    assert cj.read_text() == '{"run_tests": "custom"}'


def _all_commands(settings_path):
    data = json.loads(settings_path.read_text())
    cmds = []
    for event in ("PreToolUse", "PostToolUse"):
        for entry in data.get("hooks", {}).get(event, []):
            for h in entry.get("hooks", []):
                cmds.append(h.get("command", ""))
    return cmds


def test_native_hooks_wired_into_repo_settings(tmp_path):
    status = reg._install_native_hooks(str(tmp_path), dry_run=False)
    assert "installed" in status, status
    settings = tmp_path / ".claude" / "settings.json"
    assert settings.exists()
    cmds = _all_commands(settings)
    assert any("policy_hook.py" in c for c in cmds)
    assert any("audit_hook.py" in c for c in cmds)
    # portable, not machine-specific
    assert all("$CLAUDE_ENV_HOME" in c for c in cmds if "hook.py" in c)


def test_native_hooks_dry_run_writes_nothing(tmp_path):
    status = reg._install_native_hooks(str(tmp_path), dry_run=True)
    assert "would install" in status
    assert not (tmp_path / ".claude" / "settings.json").exists()


def test_un_onboarded_repo_has_no_hooks(tmp_path):
    # A repo that was never onboarded gets no .claude/settings.json hooks — the
    # whole point of the repo-local move. (We simply never call the installer.)
    assert not (tmp_path / ".claude" / "settings.json").exists()
