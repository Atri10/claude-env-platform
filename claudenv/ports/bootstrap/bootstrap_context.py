"""
claude-env :: Ports - Bootstrap context value object
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BootstrapContext:
    """Context passed to all bootstrap steps."""

    # Paths
    repo_dir: str
    home: str
    venv_path: str
    venv_python: str
    venv_pip: str

    # CLI flags
    with_brew: bool = False
    no_deps: bool = False
    no_venv_create: bool = False
    recreate_venv: bool = False
    force_config: bool = False
    dsn: str | None = None

    # Runtime info
    uv_available: bool = False

    # Runtime state
    env_ok: bool = True
    venv_created: bool = False
    deps_installed: bool = False
