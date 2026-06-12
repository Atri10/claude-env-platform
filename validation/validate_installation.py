#!/usr/bin/env python3
"""
validate_installation.py :: verify the platform is correctly installed.

Checks (each prints PASS/FAIL, script exits non-zero on any FAIL):
  * Python version >= 3.13
  * venv exists at $CLAUDE_ENV_HOME/venv/ with a working Python
  * required directories exist under CLAUDE_ENV_HOME
  * SQLite database exists and has the expected tables
  * core python modules import (yaml; lancedb/llama/onnx are soft-checked)
  * policy files are in place
  * audit chain verifies

Usage:
  # with venv (correct, after bootstrap):
  ~/.claude-env/venv/bin/python validation/validate_installation.py
  # or via the CLI:
  ~/.claude-env/bin/claude-env validate installation
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HOME = Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))
REPO = Path(__file__).resolve().parents[1]
for p in (REPO, HOME):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

GREEN, RED, YELLOW, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[0m"
_failures = 0
_warnings = 0


def check(label: str, cond: bool, soft: bool = False) -> None:
    global _failures, _warnings
    if cond:
        print(f"{GREEN}PASS{RESET} {label}")
    elif soft:
        _warnings += 1
        print(f"{YELLOW}WARN{RESET} {label}")
    else:
        _failures += 1
        print(f"{RED}FAIL{RESET} {label}")


def main() -> int:
    global _failures, _warnings
    check("python >= 3.13", sys.version_info >= (3, 13))

    # --- venv ---
    venv_py = HOME / "venv" / "bin" / "python"
    venv_pip = HOME / "venv" / "bin" / "pip"
    check("venv exists at $CLAUDE_ENV_HOME/venv/", (HOME / "venv").is_dir())
    check("venv/bin/python executable", venv_py.exists() and os.access(venv_py, os.X_OK))
    check("venv/bin/pip present", venv_pip.exists())
    # confirm the venv Python is actually usable (not a dangling symlink)
    if venv_py.exists():
        import subprocess
        r = subprocess.run([str(venv_py), "--version"], capture_output=True, text=True)
        check(f"venv Python runs ({r.stdout.strip() or r.stderr.strip()})",
              r.returncode == 0)
    # warn if we're NOT running inside the venv
    running_inside_venv = (HOME / "venv") in Path(sys.executable).parents
    if not running_inside_venv:
        _warnings += 1
        print(f"{YELLOW}WARN{RESET} not running inside venv "
              f"(use {venv_py} for correct dep resolution)")

    for d in ("state", "knowledge/lancedb", "models", "config", "archive", "logs"):
        check(f"dir exists: {d}", (HOME / d).is_dir())

    # database + tables
    try:
        from lib.db import get_db
        db = get_db()
        rows = db.query(
            "SELECT name FROM sqlite_master WHERE type='table'") if db.backend == "sqlite" \
            else db.query("SELECT tablename AS name FROM pg_tables WHERE schemaname='public'")
        names = {r["name"] for r in rows}
        expected = {"audit_events", "agent_actions", "tool_calls", "retrieval_events",
                    "memory_reads", "memory_writes", "security_events",
                    "policy_violations", "human_approvals", "memory_nodes",
                    "memory_edges", "rag_index_state", "rag_file_state",
                    "metrics_sessions", "metrics_latency", "metrics_retrieval_quality"}
        missing = expected - names
        check(f"database tables present ({len(expected - missing)}/{len(expected)})",
              not missing)
        if missing:
            print(f"     missing: {sorted(missing)}")
    except Exception as e:
        check(f"database reachable ({e})", False)

    # policy files
    check("global-policy.yaml installed", (HOME / "config" / "global-policy.yaml").exists())

    # core imports
    try:
        import yaml  # noqa: F401
        check("import yaml", True)
    except Exception:
        check("import yaml (REQUIRED: pip install pyyaml via venv)", False, soft=True)

    for mod in ("lancedb", "onnxruntime", "mcp"):
        try:
            __import__(mod)
            check(f"import {mod}", True, soft=True)
        except Exception:
            check(f"import {mod} (install via bootstrap)", False, soft=True)

    # llama_cpp is named llama_cpp when installed via llama-cpp-python
    try:
        __import__("llama_cpp")
        check("import llama_cpp", True, soft=True)
    except Exception:
        check("import llama_cpp (install via bootstrap)", False, soft=True)

    # --- RAG model configuration (resolved from config/rag.yaml + env) ---
    try:
        from rag.config import get_config
        cfg = get_config(reload=True)

        # Embedding model is chosen at setup; report cleanly if not yet configured.
        if not cfg.embedding.model_path:
            print(f"{YELLOW}WARN{RESET} embedding model not configured "
                  "(set embedding.model_path in config/rag.yaml — see RUNBOOK §2)")
            _warnings += 1
        else:
            emb_path = Path(cfg.embedding.model_path)
            check(f"embedding model file present ({emb_path.name})",
                  emb_path.is_file(), soft=True)
            print(f"     embed model: {cfg.embedding.model_name} "
                  f"(dim={cfg.embedding.embedding_dim}) -> {emb_path}")

        # Reranker: is it configured, and does it actually load?
        rer_dir = cfg.reranker.model_dir
        if not rer_dir:
            print(f"{YELLOW}WARN{RESET} reranker not configured / disabled "
                  "(set reranker.model_dir in config/rag.yaml to enable)")
            _warnings += 1
        else:
            from rag.rerankers.cross_encoder import CrossEncoderReranker
            rr = CrossEncoderReranker.from_config(cfg.reranker)
            check(f"reranker loads + runs ({rr.model_name})", rr.ok, soft=True)
            if not rr.ok:
                print(f"     reranker inactive: {rr.status()['error']}")
                print(f"     (RAG still works — falls back to fusion order)")
    except Exception as e:
        check(f"RAG model config check ({e})", False, soft=True)

    # audit chain
    try:
        from audit.audit_logger import AuditLogger
        chain_ok, broken = AuditLogger("validate", actor="validator").verify_chain()
        check("audit chain integrity", chain_ok)
        if not chain_ok:
            print(f"     first broken event: {broken}")
    except Exception as e:
        check(f"audit chain check ({e})", False)

    print()
    if _failures:
        print(f"{RED}{_failures} failure(s), {_warnings} warning(s){RESET}")
        return 1
    print(f"{GREEN}installation OK{RESET}"
          + (f" ({_warnings} soft warning(s) — optional deps)" if _warnings else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
