"""Tests for claudenv.logging_config rotating logging setup."""
from __future__ import annotations

import logging
import logging.handlers

import pytest

import claudenv.logging_config as lc

ROOT = logging.getLogger()


@pytest.fixture(autouse=True)
def _reset_logging(monkeypatch, tmp_path):
    """Isolate logging: clear handlers on root + app logger, route home to tmp."""
    for logger in (ROOT, logging.getLogger(lc.APP_LOGGER_NAME)):
        for h in list(logger.handlers):
            logger.removeHandler(h)
            h.close()
    monkeypatch.setattr(lc, "_configured", False)
    monkeypatch.setenv("CLAUDE_ENV_HOME", str(tmp_path))
    yield
    for logger in (ROOT, logging.getLogger(lc.APP_LOGGER_NAME)):
        for h in list(logger.handlers):
            logger.removeHandler(h)
            h.close()


def _file_handlers():
    return [h for h in ROOT.handlers if isinstance(h, logging.handlers.RotatingFileHandler)]


def _console_handlers():
    return [h for h in ROOT.handlers if getattr(h, "_claudenv_console", False)]


def test_log_file_lives_under_home(tmp_path):
    lc.configure_logging()
    path = lc.log_file_path()
    assert path == tmp_path / "logs" / "claudenv.log"
    assert path.parent.exists()


def test_single_rotating_file_handler(tmp_path):
    lc.configure_logging()
    rh = _file_handlers()
    assert len(rh) == 1
    assert rh[0].maxBytes == lc.DEFAULT_MAX_BYTES
    assert rh[0].backupCount == lc.DEFAULT_BACKUP_COUNT


def test_file_handler_is_idempotent(tmp_path):
    lc.configure_logging()
    first = len(_file_handlers())
    lc.configure_logging()
    second = len(_file_handlers())
    assert first == 1 and second == 1


def test_console_handler_toggle(tmp_path):
    lc.configure_logging(console=False)
    assert not _console_handlers()
    lc.configure_logging(console=True)
    assert _console_handlers()


def test_records_are_written_to_file(tmp_path):
    lc.configure_logging()
    logging.getLogger("claudenv.test").info("verify-message")
    assert "verify-message" in lc.log_file_path().read_text()


def test_verbose_lowers_file_level(tmp_path):
    lc.configure_logging(verbose=True)
    rh = _file_handlers()[0]
    assert rh.level == logging.DEBUG
    lc.configure_logging(verbose=False)
    assert rh.level == logging.INFO


def test_rotation_creates_numbered_backup(tmp_path):
    lc.configure_logging()
    rh = _file_handlers()[0]
    rh.doRollover()
    backup = lc.log_file_path().parent / (lc.log_file_path().name + ".1")
    assert backup.exists()


def test_filter_excludes_third_party(tmp_path):
    lc.configure_logging(verbose=True)
    logging.getLogger("claudenv.thing").warning("own-record")
    logging.getLogger("lancedb").warning("foreign-record")
    text = lc.log_file_path().read_text()
    assert "own-record" in text
    assert "foreign-record" not in text
