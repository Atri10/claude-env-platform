"""Coverage for bootstrap.py's TTL reaper for stale scratch directories.
Run: pytest tests/ -q

Regression context: filesystem-policy wipes+recreates its repo's scratch dir
on every process start (mcp-servers/filesystem-policy/server.py's
_reset_scratch_dir, called from __main__), and attempts atexit/signal cleanup
on shutdown -- but neither covers SIGKILL, an OOM kill, or a host crash. This
reaper is the backstop: any bootstrap.py run (or a future periodic health
check) sweeps $CLAUDE_ENV_HOME/scratch/* and removes directories whose mtime
exceeds a TTL, so an abandoned scratch dir doesn't accumulate disk usage
indefinitely on a machine that never cleanly restarts its MCP servers.
"""
import importlib.util
import os
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("bootstrap_under_test_reap",
                                               _ROOT / "bootstrap.py")
bootstrap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bootstrap)


def _make_scratch_dir(home: Path, name: str, age_hours: float) -> Path:
    d = home / "scratch" / name
    d.mkdir(parents=True)
    (d / "leftover.txt").write_text("x\n")
    old_time = time.time() - age_hours * 3600
    os.utime(d, (old_time, old_time))
    return d


def test_reaps_directories_older_than_ttl(tmp_path):
    home = tmp_path / "home"
    stale = _make_scratch_dir(home, "old-repo", age_hours=48)
    fresh = _make_scratch_dir(home, "new-repo", age_hours=1)

    removed = bootstrap.reap_stale_scratch_dirs(home, ttl_hours=24)

    assert "old-repo" in removed
    assert "new-repo" not in removed
    assert not stale.exists()
    assert fresh.exists()


def test_no_scratch_dir_is_a_noop(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    removed = bootstrap.reap_stale_scratch_dirs(home, ttl_hours=24)
    assert removed == []


def test_ttl_env_override(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _make_scratch_dir(home, "medium-repo", age_hours=2)

    monkeypatch.setenv("CLAUDE_ENV_SCRATCH_TTL_HOURS", "1")
    removed = bootstrap.reap_stale_scratch_dirs(home)

    assert "medium-repo" in removed
