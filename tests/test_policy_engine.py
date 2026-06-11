"""Minimal pytest coverage for the policy engine glob + decision logic.
Run: pytest tests/ -q   (from repo root)"""
import sys, tempfile, os, shutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from security.policy_engine import PolicyEngine, _pmatch

def _engine():
    d = tempfile.mkdtemp(); os.makedirs(d + "/.claude")
    root = Path(__file__).resolve().parents[1]
    shutil.copy(root / "config" / "repo-policy.template.yaml", d + "/.claude/repo-policy.yaml")
    return PolicyEngine.load(d, root / "config" / "global-policy.yaml")

def test_glob_double_star():
    assert _pmatch("a/b/c.py", "**/c.py")
    assert _pmatch("c.py", "**/c.py")           # **/ matches zero segments
    assert _pmatch("src/x.py", "src/**")
    assert not _pmatch("srcx/x.py", "src/**")
    assert not _pmatch("a/b.py", "*.py")        # * does not cross '/'

def test_blocks_secrets():
    pe = _engine()
    for p in [".env", ".env.local", "secrets/k.pem", "deploy/id_rsa", "config/production.yaml"]:
        assert pe.evaluate_path(p).action == "block", p

def test_allows_source():
    pe = _engine()
    for p in ["src/app.py", "docs/x.md", "tests/test_app.py", "README.md"]:
        assert pe.evaluate_path(p).action == "allow", p

def test_content_redaction():
    pe = _engine()
    out, hits = pe.scan_content('key="AKIAABCDEFGHIJKLMNOP"')
    assert hits and "AKIA" not in out
