"""Tests for claudenv.logging_config TimedRotatingFileHandler setup."""
from __future__ import annotations

import logging
import logging.handlers

import pytest

import claudenv.adapters.logging as lc

ROOT = logging.getLogger()

_TIMED_ROTATING = logging.handlers.TimedRotatingFileHandler


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
    return [h for h in ROOT.handlers if isinstance(h, _TIMED_ROTATING)]


def _console_handlers():
    return [h for h in ROOT.handlers if isinstance(h, lc._ConsoleHandler)]


def test_log_file_lives_under_home(tmp_path):
    lc.configure_logging()
    path = lc.log_file_path()
    assert path == tmp_path / "logs" / "claudenv.log"
    assert path.parent.exists()


def test_single_timed_rotating_file_handler(tmp_path):
    lc.configure_logging()
    rh = _file_handlers()
    assert len(rh) == 1
    assert rh[0].when.upper() == "MIDNIGHT"
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


def test_rotation_is_configured(tmp_path):
    lc.configure_logging()
    rh = _file_handlers()[0]
    assert isinstance(rh, _TIMED_ROTATING)
    assert rh.suffix is not None
    assert rh.when.upper() == "MIDNIGHT"
    assert rh.backupCount == lc.DEFAULT_BACKUP_COUNT


def test_filter_excludes_third_party(tmp_path):
    lc.configure_logging(verbose=True)
    logging.getLogger("claudenv.thing").warning("own-record")
    logging.getLogger("lancedb").warning("foreign-record")
    text = lc.log_file_path().read_text()
    assert "own-record" in text
    assert "foreign-record" not in text
