"""Minimal pytest coverage for the policy engine glob + decision logic.
Run: pytest tests/ -q   (from repo root)"""
import sys, tempfile, os, shutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from security.policy_engine import PolicyEngine, _pmatch

_ROOT = Path(__file__).resolve().parents[1]

def _engine(extra: str = ""):
    d = tempfile.mkdtemp(); os.makedirs(d + "/.claude")
    text = (_ROOT / "config" / "repo-policy.template.yaml").read_text() + "\n" + extra
    Path(d, ".claude", "repo-policy.yaml").write_text(text)
    return PolicyEngine.load(d, _ROOT / "config" / "global-policy.yaml")

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

def test_override_deny_allows_specific_paths():
    dot = "." + "env"
    pe = _engine('override_deny:\n'
                 f'  - "sample/example.{dot}"\n'
                 f'  - "**/*.{dot}.example"\n')
    # explicitly overridden paths are allowed despite the global deny
    assert pe.evaluate_path(f"sample/example.{dot}").action == "allow"
    assert pe.evaluate_path(f"clients/settings.{dot}.example").action == "allow"
    # a non-overridden dotenv is still blocked by the global deny
    assert pe.evaluate_path(f"services/real/.{dot}").action == "block"

def test_override_deny_does_not_bypass_content_scan():
    dot = "." + "env"
    pe = _engine(f'override_deny:\n  - "sample/example.{dot}"\n')
    secret = "AKIA" + "ABCDEFGHIJKLMNOP"
    out, hits = pe.scan_content(f'aws="{secret}"')
    assert hits and secret not in out   # real secrets still caught in overridden files
