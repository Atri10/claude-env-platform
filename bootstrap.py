#!/usr/bin/env python3
"""
bootstrap.py :: one-command setup for the local AI dev platform.

Idempotent. Safe to re-run. All dependencies are installed into a dedicated
virtual environment at $CLAUDE_ENV_HOME/venv/ — no global site-packages are
touched and no system Python is modified.

Steps (in order):
  1. environment checks   (macOS arch, Python >=3.13, Homebrew, git)
  2. directory layout     (~/.claude-env/{state,venv,knowledge,models,...})
  3. venv creation        ($CLAUDE_ENV_HOME/venv/ — isolated, folder-only)
  4. dependency install   (into the venv via venv/bin/pip)
  5. optional brew        (llama.cpp formula if --with-brew)
  6. SQLite init          (applies sql/001_schema.sql + sql/002_retention.sql)
  7. LanceDB init         (creates the knowledge/lancedb directory)
  8. policy init          (copies global-policy.yaml + repo-policy template)
  9. audit init           (writes genesis event, verifies the chain)
 10. environment validate  (delegates to validation/validate_installation.py)

Usage:
    python3 bootstrap.py                   # full bootstrap
    python3 bootstrap.py --with-brew       # also install brew formulae
    python3 bootstrap.py --no-deps         # skip dep install (venv must exist)
    python3 bootstrap.py --no-venv-create  # reuse existing venv, just update deps
    python3 bootstrap.py --dsn postgresql://user@localhost/claude_env  # PG target

After bootstrap, every platform script should be run via the venv Python:
    ~/.claude-env/venv/bin/python <script>
Or use the convenience CLI (which auto-resolves the venv):
    ~/.claude-env/bin/claude-env <command>
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import venv as _venv
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
HOME = Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))
VENV = HOME / "venv"
VENV_PY = VENV / "bin" / "python"
VENV_PIP = VENV / "bin" / "pip"

DIRS = [
    "state", "venv",
    "knowledge", "knowledge/lancedb", "knowledge/docs",
    "models",
    "archive", "archive/memory", "archive/audit", "archive/security",
    "logs", "config", "bin",
]

PIP_DEPS_LOCKED = [
    "pyyaml==6.0.3",
    "lancedb==0.33.0",
    "pyarrow==24.0.0",
    "llama-cpp-python==0.3.26",
    "onnxruntime==1.26.0",
    "transformers==4.41.0",
    "tree-sitter==0.22.0",
    "tree-sitter-languages==1.10.2",
    "mcp==1.0.0",
    "httpx==0.27.0",
    "datasette==0.64.0",
]

BREW_FORMULAE = ["llama.cpp", "git", "sqlite"]

GREEN, RED, YELLOW, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[0m"


def ok(msg: str) -> None:
    print(f"{GREEN}[ok]{RESET} {msg}")


def warn(msg: str) -> None:
    print(f"{YELLOW}[warn]{RESET} {msg}")


def fail(msg: str) -> None:
    print(f"{RED}[fail]{RESET} {msg}")


# ---------------------------------------------------------------------------
# 1. environment checks
# ---------------------------------------------------------------------------
def check_environment() -> bool:
    good = True
    sysname = platform.system()
    arch = platform.machine()
    if sysname == "Darwin" and arch == "arm64":
        ok(f"platform: macOS {arch} (Metal acceleration available)")
    elif sysname == "Linux":
        ok(f"platform: Linux {arch} (CPU inference; schedule jobs via "
           f"scripts/systemd/ instead of launchd)")
    else:
        warn(f"platform is {sysname}/{arch}; primary targets are macOS arm64 "
             f"and Linux (continuing)")

    if sys.version_info >= (3, 13):
        ok(f"python {platform.python_version()} (bootstrap interpreter)")
    else:
        fail(f"python {platform.python_version()} < 3.13 required")
        good = False

    if shutil.which("git"):
        ok("git present")
    else:
        fail("git not found on PATH")
        good = False

    if shutil.which("brew"):
        ok("homebrew present")
    else:
        warn("homebrew not found (needed for --with-brew)")
    return good


# ---------------------------------------------------------------------------
# 2. directory layout
# ---------------------------------------------------------------------------
def make_dirs() -> None:
    for d in DIRS:
        (HOME / d).mkdir(parents=True, exist_ok=True)
    ok(f"directory layout under {HOME}")


# ---------------------------------------------------------------------------
# 3. venv creation
# ---------------------------------------------------------------------------
def create_venv(recreate: bool = False) -> None:
    if VENV_PY.exists() and not recreate:
        ok(f"venv already exists at {VENV}")
        return
    print(f"creating venv at {VENV} …")
    _venv.create(str(VENV), with_pip=True, clear=recreate, symlinks=True)
    ok(f"venv created: {VENV_PY}")


# ---------------------------------------------------------------------------
# 4. dependency install (into the venv — no global site-packages modified)
# ---------------------------------------------------------------------------
def install_deps(with_brew: bool) -> None:
    if not VENV_PIP.exists():
        fail("venv pip not found — run without --no-venv-create first")
        return

    print("installing python dependencies into venv …")
    r = subprocess.run([str(VENV_PIP), "install", "--upgrade", *PIP_DEPS])
    if r.returncode:
        warn("pip install reported errors; review output above")
    else:
        ok("python dependencies installed into venv")

    if with_brew:
        if shutil.which("brew"):
            print("installing brew formulae …")
            subprocess.run(["brew", "install", *BREW_FORMULAE])
            ok("brew formulae processed")
        elif platform.system() == "Linux":
            warn("--with-brew: no Homebrew on this host — install equivalents "
                 "via your package manager (e.g. apt install git sqlite3; "
                 "llama.cpp builds from source or via the pip wheel)")

    # llama.cpp Python binding: Metal flag on Apple Silicon, plain build elsewhere
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        print("installing llama-cpp-python with Metal acceleration …")
        env = {**os.environ, "CMAKE_ARGS": "-DLLAMA_METAL=on"}
        subprocess.run(
            [str(VENV_PIP), "install", "--upgrade", "--force-reinstall",
             "llama-cpp-python"],
            env=env)
        ok("llama-cpp-python (Metal) installed")


# ---------------------------------------------------------------------------
# 5. SQLite / persistence
# ---------------------------------------------------------------------------
def init_database(dsn: str | None) -> None:
    if dsn:
        os.environ["CLAUDE_ENV_DSN"] = dsn
    sys.path.insert(0, str(REPO_DIR))
    from lib.db import get_db
    db = get_db()
    db.apply_schema(str(REPO_DIR / "sql" / "001_schema.sql"),
                    str(REPO_DIR / "sql" / "002_retention.sql"),
                    str(REPO_DIR / "sql" / "003_extensions.sql"))
    ok(f"database schema applied ({db.backend})")


# ---------------------------------------------------------------------------
# 6. LanceDB
# ---------------------------------------------------------------------------
def init_lancedb() -> None:
    path = HOME / "knowledge" / "lancedb"
    path.mkdir(parents=True, exist_ok=True)
    # Use the venv's Python to test the import (lancedb may not be in bootstrap's Python)
    r = subprocess.run([str(VENV_PY), "-c",
                        "import lancedb; lancedb.connect('.')"],
                       capture_output=True, cwd=str(path))
    if r.returncode == 0:
        ok(f"lancedb initialized at {path}")
    else:
        warn("lancedb not importable in venv; directory created, run --with-brew or "
             "check pip output above")


# ---------------------------------------------------------------------------
# 7. policies
# ---------------------------------------------------------------------------
def init_policies() -> None:
    src_global = REPO_DIR / "config" / "global-policy.yaml"
    dst_global = HOME / "config" / "global-policy.yaml"
    if src_global.exists():
        shutil.copy2(src_global, dst_global)
        ok(f"global policy -> {dst_global}")
    else:
        warn("config/global-policy.yaml missing in repo")

    tmpl = REPO_DIR / "config" / "repo-policy.template.yaml"
    dst_tmpl = HOME / "config" / "repo-policy.template.yaml"
    if tmpl.exists():
        shutil.copy2(tmpl, dst_tmpl)
        ok(f"repo-policy template -> {dst_tmpl}")

    # RAG model config — rag/config.py resolves config/rag.yaml relative to the
    # deployed tree, so it must live at $CLAUDE_ENV_HOME/config/rag.yaml.
    src_rag = REPO_DIR / "config" / "rag.yaml"
    dst_rag = HOME / "config" / "rag.yaml"
    if src_rag.exists():
        shutil.copy2(src_rag, dst_rag)
        ok(f"rag model config -> {dst_rag}")
    else:
        warn("config/rag.yaml missing in repo (RAG will use built-in defaults)")

    # mirror the platform code under $CLAUDE_ENV_HOME so MCP servers can import it
    for sub in ("security", "audit", "lib", "rag", "memory", "observability",
                "agents", "mcp-servers", "sql", "validation", "scripts", "hooks"):
        s = REPO_DIR / sub
        d = HOME / sub
        if s.exists():
            if d.exists():
                shutil.rmtree(d)
            shutil.copytree(s, d, ignore=shutil.ignore_patterns("__pycache__"))

    # install the CLI into $CLAUDE_ENV_HOME/bin/claude-env
    src_cli = REPO_DIR / "bin" / "claude-env"
    if src_cli.exists():
        dst_cli = HOME / "bin" / "claude-env"
        shutil.copy2(src_cli, dst_cli)
        dst_cli.chmod(0o755)
        ok(f"CLI installed -> {dst_cli}")

    ok(f"platform code mirrored under {HOME}")


# ---------------------------------------------------------------------------
# 8. audit genesis
# ---------------------------------------------------------------------------
def init_audit() -> None:
    sys.path.insert(0, str(REPO_DIR))
    from audit.audit_logger import AuditLogger
    log = AuditLogger(session_id="bootstrap", actor="bootstrap")
    log.agent_action(agent="bootstrap", action="initialize",
                     summary="platform bootstrap complete", success=True)
    chain_ok, broken = log.verify_chain()
    if chain_ok:
        ok("audit chain initialized and verified")
    else:
        fail(f"audit chain verification failed at event {broken}")


# ---------------------------------------------------------------------------
# 9. validate (via the venv Python so all deps are present)
# ---------------------------------------------------------------------------
def validate() -> int:
    script = REPO_DIR / "validation" / "validate_installation.py"
    if not script.exists():
        warn("validate_installation.py not found; skipping final validation")
        return 0
    return subprocess.run([str(VENV_PY), str(script)]).returncode


# ---------------------------------------------------------------------------
# activation hint
# ---------------------------------------------------------------------------
def print_activation_hint() -> None:
    rel = VENV.relative_to(Path.home()) if VENV.is_relative_to(Path.home()) else VENV
    print(f"""
{GREEN}Venv location:{RESET} {VENV}
{GREEN}Venv Python:  {RESET} {VENV_PY}

To use the venv directly (optional — the platform handles this automatically):
  source ~/{rel}/bin/activate

Or use the platform CLI (resolves the venv for you):
  {HOME}/bin/claude-env validate all
  {HOME}/bin/claude-env dashboard

To run any platform script manually:
  {VENV_PY} validation/validate_installation.py
  {VENV_PY} rag/bootstrap_rag.py /path/to/repo

Add to ~/.zshrc for convenience:
  export CLAUDE_ENV_HOME="{HOME}"
  export PATH="$CLAUDE_ENV_HOME/bin:$PATH"
""")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
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
    ap.add_argument("--dsn", default=None,
                    help="override persistence DSN (default: SQLite)")
    args = ap.parse_args()

    print(f"== claude-env bootstrap ==\nHOME = {HOME}\nVENV = {VENV}\n")

    env_ok = check_environment()
    make_dirs()

    if not args.no_venv_create:
        create_venv(recreate=args.recreate_venv)

    if not args.no_deps:
        install_deps(args.with_brew)

    # DB init runs via bootstrap Python (lib/db uses stdlib sqlite3 — no venv dep)
    init_database(args.dsn)
    init_lancedb()
    init_policies()
    init_audit()

    # final validation runs via the venv Python (so all deps are tested)
    rc = validate()

    print()
    if env_ok and rc == 0:
        ok("bootstrap complete")
    else:
        warn("bootstrap finished with warnings; review output above")

    print_activation_hint()
    return 0 if (env_ok and rc == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
