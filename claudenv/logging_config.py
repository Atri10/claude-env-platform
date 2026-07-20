"""
claude-env :: Application-wide logging configuration

claude-env is a multi-process application: the ``claude-env`` CLI, the native
tool hooks, and the MCP servers are each launched as separate processes (often
via ``python -m claudenv...``, where ``__name__ == "__main__"``). To get
consistent, professional logging across all of them, every process calls
:func:`configure_logging` once at startup (the file handler is installed
idempotently, so re-entry is safe).

What it does
------------
* Attaches a :class:`logging.handlers.RotatingFileHandler` to the **root**
  logger, gated by a filter that admits only ``claudenv.*`` records (plus the
  ``__main__`` entry module, so ``python -m`` invocations are covered too).
  Third-party libraries are intentionally excluded from the operational log.
* Optionally mirrors records to ``stderr`` via a console handler (used by the
  CLI; suppressed for stdio subprocesses such as the MCP servers/hooks, where
  stdout carries the protocol — stderr is still free, but the file is the
  canonical record).
* Installs an exception hook so unhandled exceptions (main thread + threads)
  are captured to the operational log instead of being lost on a bare
  traceback.

Usage
-----
Modules obtain a logger with the standard idiom::

    import logging

    logger = logging.getLogger(__name__)

Because the filter admits any ``claudenv.*`` name, every module under the
package is captured automatically — regardless of whether the process was
started through the installed ``claude-env`` script or ``python -m``.

The module is intentionally dependency-free (only the stdlib + the documented
``CLAUDE_ENV_HOME`` convention) so it can be imported from any entry point
without pulling in the rest of the package.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import threading
from pathlib import Path

APP_LOGGER_NAME = "claudenv"
DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB per rotated file
DEFAULT_BACKUP_COUNT = 5

_FILE_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-8s %(name)s %(threadName)s %(message)s"
_CONSOLE_FORMAT = "%(levelname)-8s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"

_configured = False


class _ClaudenvFilter(logging.Filter):
    """Admit only records originating from claude-env code.

    Covers both ``claudenv.*`` loggers and the ``__main__`` entry module (which
    is what ``python -m claudenv...`` produces), while excluding third-party
    library noise from the operational log.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        name = record.name
        return name == "__main__" or name.startswith("claudenv")


def _claude_env_home() -> Path:
    return Path(os.environ.get("CLAUDE_ENV_HOME", Path.home() / ".claude-env"))


def log_file_path() -> Path:
    """Absolute path of the rotating application log file."""
    return _claude_env_home() / "logs" / "claudenv.log"


def _build_file_handler() -> logging.Handler:
    path = log_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        path,
        maxBytes=DEFAULT_MAX_BYTES,
        backupCount=DEFAULT_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(_FILE_FORMAT, datefmt=_DATE_FORMAT))
    handler.addFilter(_ClaudenvFilter())
    return handler


def _install_exception_hook(logger: logging.Logger) -> None:
    """Route unhandled exceptions (main thread + threads) to the log."""

    def _hook(etype: object, value: object, tb: object) -> None:
        logger.error("Unhandled exception", exc_info=(etype, value, tb))  # type: ignore[arg-type]

    sys.excepthook = _hook  # type: ignore[assignment]

    def _thread_hook(args: threading.ExceptHookArgs) -> None:
        logger.error(
            "Unhandled exception in thread",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = _thread_hook  # type: ignore[assignment]


def configure_logging(
    *,
    level: int | str = logging.INFO,
    verbose: bool = False,
    console: bool = True,
) -> logging.Logger:
    """Configure claude-env operational logging.

    Idempotent for the file handler: repeated calls keep a single rotating file
    handler but re-apply the console/level flags, so a re-invoked CLI can honour
    its ``--verbose`` flag.

    Args:
        level: Base level for the rotating file handler (``INFO`` default,
            ``DEBUG`` when ``verbose`` is set).
        verbose: Emit ``DEBUG`` to both file and console.
        console: Attach a stderr handler. Pass ``False`` for stdio subprocesses
            (MCP servers / native hooks) so logs stay in the file only and do
            not pollute the protocol channel.
    """
    global _configured
    root = logging.getLogger()

    if not _configured:
        root.setLevel(logging.DEBUG)  # handlers do the real filtering
        root.addHandler(_build_file_handler())
        _install_exception_hook(logging.getLogger(APP_LOGGER_NAME))
        _configured = True

    # (Re)apply the console handler so re-invocation honours current flags.
    for h in list(root.handlers):
        if getattr(h, "_claudenv_console", False):
            root.removeHandler(h)
    if console:
        ch = logging.StreamHandler(sys.stderr)
        ch._claudenv_console = True  # type: ignore[attr-defined]
        ch.setLevel(logging.DEBUG if verbose else logging.WARNING)
        ch.setFormatter(logging.Formatter(_CONSOLE_FORMAT, datefmt=_DATE_FORMAT))
        ch.addFilter(_ClaudenvFilter())
        root.addHandler(ch)

    file_level = logging.DEBUG if verbose else level
    if isinstance(file_level, str):
        file_level = logging.getLevelName(file_level)
    for h in root.handlers:
        if isinstance(h, logging.handlers.RotatingFileHandler):
            h.setLevel(file_level)

    return logging.getLogger(APP_LOGGER_NAME)
