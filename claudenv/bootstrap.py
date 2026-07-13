#!/usr/bin/env python3
"""
claude-env :: Bootstrap
One-command setup for the local AI dev platform.

Idempotent. Safe to re-run. All dependencies installed into a dedicated
virtual environment at $CLAUDE_ENV_HOME/venv/ — no global site-packages touched.
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import sys
from pathlib import Path

from claudenv.adapters.bootstrap import (
    BootstrapOrchestrator,
    BootstrapStepFactory,
)
from claudenv.ports.bootstrap import BootstrapContext


# UV is preferred for venv creation and dependency install (faster, better resolver).
# Falls back to stdlib venv + venv/bin/pip when uv is unavailable.
UV = shutil.which("uv")

# Module-level constants for paths
REPO_DIR = Path(__file__).resolve().parents[1]
HOME = Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))
VENV = HOME / "venv"
VENV_PY = VENV / "bin" / "python"
VENV_PIP = VENV / "bin" / "pip"


def build_context(args: argparse.Namespace) -> BootstrapContext:
    """Build immutable bootstrap context from CLI arguments."""
    return BootstrapContext(
        home=str(HOME),
        repo_dir=str(REPO_DIR),
        venv_path=str(VENV),
        venv_python=str(VENV_PY),
        venv_pip=str(VENV_PIP),
        dsn=args.dsn,
        force_config=args.force_config,
        with_brew=args.with_brew,
        recreate_venv=args.recreate_venv,
        no_deps=args.no_deps,
        no_venv_create=args.no_venv_create,
        uv_available=UV is not None,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Bootstrap the local AI dev platform")
    ap.add_argument("--with-brew", action="store_true",
                    help="also install Homebrew formulae (llama.cpp, git, sqlite)")
    ap.add_argument("--no-deps", action="store_true",
                    help="skip pip install (venv must already exist)")
    ap.add_argument("--no-venv-create", action="store_true",
                    help="reuse existing venv, only update deps inside it")
    ap.add_argument("--recreate-venv", action="store_true",
                    help="delete and recreate the venv (use after Python upgrade)")
    ap.add_argument("--force-config", action="store_true",
                    help="reset global-policy.yaml/rag.yaml/mcp-servers.json/budgets.yaml "
                         "to the repo's template, discarding any local edits to the "
                         "deployed copies")
    ap.add_argument("--dsn", default=None,
                    help="override persistence DSN (default: SQLite)")
    args = ap.parse_args()

    print(f"== claude-env bootstrap ==\nHOME = {HOME}\nVENV = {VENV}\n")

    context = build_context(args)
    orchestrator = BootstrapOrchestrator()

    # Create steps via factory (DIP: depends on abstraction)
    factory = BootstrapStepFactory(uv_available=context.uv_available)
    for step in factory.create_steps():
        orchestrator.add_step(step)

    # Execute pipeline
    results = orchestrator.execute(context)

    # Determine overall success
    env_ok = results[0].success if results else True  # First step is environment check
    validation_ok = all(r.success for r in results if r.message != "activation-hint: skipped")

    print()
    if env_ok and validation_ok:
        print("\033[32m[ok]\033[0m bootstrap complete")
        return 0
    else:
        print("\033[33m[warn]\033[0m bootstrap finished with warnings; review output above")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())