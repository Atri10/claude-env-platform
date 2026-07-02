"""Coverage for onboarding helpers that don't need a model.
Run: pytest tests/ -q"""
import importlib.util
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
