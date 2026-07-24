"""
claude-env :: packaged default data (config, sql, templates).

These are the defaults shipped inside the wheel. At runtime, user-editable
copies live under ``$CLAUDE_ENV_HOME/`` (populated by ``claude-env init``) and
take precedence; these packaged copies are the fallback / the source that
``init`` copies from.

Use :func:`data_path` to get a real filesystem path to a packaged resource. It
works both from a source checkout and from an installed wheel (extracting to a
temp dir if the package is zipped).
"""
from __future__ import annotations

import atexit
from contextlib import ExitStack
from importlib import resources
from pathlib import Path

# Keep extracted-resource temp dirs alive for the process lifetime.
_RESOURCE_STACK = ExitStack()
atexit.register(_RESOURCE_STACK.close)


def data_path(*parts: str) -> Path:
    """Return a real filesystem Path to a packaged data resource.

    ``data_path("sql", "schema.sql")`` -> path to that file inside
    ``claudenv/_data/``. Works from a source tree and from an installed
    (possibly zipped) wheel.
    """
    resource = resources.files(__package__)
    for part in parts:
        resource = resource / part
    # as_file yields a real path, extracting from a zip if needed.
    return _RESOURCE_STACK.enter_context(resources.as_file(resource))


def config_dir() -> Path:
    """Filesystem path to the packaged default config directory."""
    return data_path("config")


def sql_dir() -> Path:
    """Filesystem path to the packaged SQL schema directory."""
    return data_path("sql")


def templates_dir() -> Path:
    """Filesystem path to the packaged repo-onboarding templates directory."""
    return data_path("templates")
