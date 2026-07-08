"""Coverage for lib/logging_setup.py. Run: pytest tests/ -q

The shared logger replaced silent `except: pass` swallows across the platform.
These tests lock in the properties those call sites rely on: a rolling file is
actually written, repeat calls don't stack handlers, stdout is never a target
(MCP stdio safety), and setup never raises even when the log dir is unwritable.
"""
import importlib
import logging
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


def _fresh_module(home: str):
    os.environ["CLAUDE_ENV_HOME"] = home
    import lib.logging_setup as ls
    importlib.reload(ls)  # re-read _HOME/_LOG_DIR from the env
    return ls


def test_writes_rolling_file():
    home = tempfile.mkdtemp()
    ls = _fresh_module(home)
    log = ls.get_logger("unit-a")
    log.warning("a message")
    logfile = Path(home) / "logs" / "unit-a.log"
    assert logfile.exists()
    assert "a message" in logfile.read_text()


def test_idempotent_no_duplicate_handlers():
    home = tempfile.mkdtemp()
    ls = _fresh_module(home)
    first = ls.get_logger("unit-b")
    n = len(first.handlers)
    again = ls.get_logger("unit-b")
    assert again is first
    assert len(again.handlers) == n  # no stacking


def test_never_targets_stdout():
    home = tempfile.mkdtemp()
    ls = _fresh_module(home)
    log = ls.get_logger("unit-c", stderr=True)
    for h in log.handlers:
        stream = getattr(h, "stream", None)
        assert stream is not sys.stdout
    assert log.propagate is False  # won't bubble to root's stdout handler


def test_rotation_configured():
    home = tempfile.mkdtemp()
    ls = _fresh_module(home)
    log = ls.get_logger("unit-d")
    from logging.handlers import RotatingFileHandler
    rfh = [h for h in log.handlers if isinstance(h, RotatingFileHandler)]
    assert rfh, "expected a RotatingFileHandler"
    assert rfh[0].backupCount >= 1 and rfh[0].maxBytes > 0


def test_setup_never_raises_on_unwritable_dir(tmp_path):
    # point CLAUDE_ENV_HOME at a *file* so logs/ can't be created as a dir
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    ls = _fresh_module(str(blocker))
    # must not raise, and must still return a usable logger (stderr/null fallback)
    log = ls.get_logger("unit-e")
    log.warning("should not explode")
    assert isinstance(log, logging.Logger)
    assert log.handlers  # at least a fallback handler present
