"""
claude-env :: shared logging setup
File: lib/logging_setup.py
Purpose:
    One place to obtain a configured logger so the platform stops swallowing
    exceptions silently (`except ...: pass`). Every subsystem writes to its own
    size-rolling file under $CLAUDE_ENV_HOME/logs/<name>.log, so a failure that
    was previously invisible now leaves an evidence trail without changing
    control flow (best-effort paths stay best-effort — they just get logged).

Design / invariants:
    * File-first, stdout-NEVER. MCP servers speak the stdio protocol on stdout;
      a stray log line there corrupts the channel. Handlers here write to a
      rotating file and (optionally) stderr — never stdout. `propagate=False`
      keeps records off the root logger's default stdout handler too.
    * Fail-safe. If the logs directory can't be created or opened (read-only FS,
      perms), we fall back to a stderr handler and, failing even that, return a
      logger with a NullHandler. Configuring logging must not raise — that would
      turn an observability nicety into an outage.
    * Idempotent. Repeated get_logger("x") calls reuse the same configured
      logger instead of stacking duplicate handlers.
    * Local-only. Plain file I/O; no network egress (honours the platform's
      no-egress invariant).

Usage:
    from lib.logging_setup import get_logger
    log = get_logger("memory")           # -> $CLAUDE_ENV_HOME/logs/memory.log
    try:
        risky()
    except SomeError:
        log.warning("risky() failed; continuing with fallback", exc_info=True)

Note on sensitive data: prefer logging the operation + exception type/message
over dumping node bodies, file contents, or tool payloads. Bodies are already
secret-redacted before storage, but logs are a separate sink — keep them lean.
"""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_HOME = Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))
_LOG_DIR = _HOME / "logs"

# Rolling-file policy (size-based). Overridable via env for ops tuning.
_MAX_BYTES = int(os.environ.get("CLAUDE_ENV_LOG_MAX_BYTES", 5 * 1024 * 1024))  # 5 MB
_BACKUPS = int(os.environ.get("CLAUDE_ENV_LOG_BACKUPS", 5))
_LEVEL = os.environ.get("CLAUDE_ENV_LOG_LEVEL", "INFO").upper()
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s :: %(message)s"

_CONFIGURED: set[str] = set()


def _safe_name(name: str) -> str:
    """Filesystem-safe log filename stem (no separators/spaces)."""
    return "".join(c if (c.isalnum() or c in "-_.") else "-" for c in name) or "claude-env"


def get_logger(name: str, *, level: str | int | None = None,
               stderr: bool = False) -> logging.Logger:
    """Return a size-rolling file logger for `name`.

    name    : subsystem label; becomes $CLAUDE_ENV_HOME/logs/<name>.log
    level   : override the default level (env CLAUDE_ENV_LOG_LEVEL, else INFO)
    stderr  : also mirror to stderr (safe for MCP servers; stdout is never used)
    """
    logger = logging.getLogger(f"claude-env.{name}")
    logger.setLevel(level or _LEVEL)
    logger.propagate = False  # keep records off the root logger (its default
                              # StreamHandler targets stdout — unsafe for MCP)

    if name in _CONFIGURED and logger.handlers:
        return logger

    fmt = logging.Formatter(_FORMAT)

    # Primary sink: rolling file. Guarded so a logging problem never propagates.
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            _LOG_DIR / f"{_safe_name(name)}.log",
            maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception:  # noqa: BLE001 — fall back, never raise from logging setup
        stderr = True

    if stderr:
        sh = logging.StreamHandler(sys.stderr)  # stderr only, never stdout
        sh.setFormatter(fmt)
        logger.addHandler(sh)

    if not logger.handlers:
        logger.addHandler(logging.NullHandler())

    _CONFIGURED.add(name)
    return logger
