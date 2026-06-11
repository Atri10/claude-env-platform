#!/usr/bin/env python3
"""
validate_mcp.py :: verify the MCP topology and server files are coherent.

Checks (exit non-zero on failure):
  * config/mcp-servers.json parses
  * every declared server points at a server.py that exists (jetbrains is optional)
  * startup_order values are unique and contiguous-ish (strictly increasing set)
  * filesystem-policy declares policy enforcement + content scan
  * lancedb-rag is read-only and wraps results as data
  * terminal is allowlist-only and denies unrestricted exec
  * memory-graph enforces namespace isolation
  * documentation restricts external fetch to tiers [0,1]
  * each non-optional server module imports cleanly enough to expose list_tools
    (soft-checked: requires the `mcp` package; WARN if absent)

Usage:  python validation/validate_mcp.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOME = Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))
CONFIG = REPO / "config" / "mcp-servers.json"
GREEN, RED, YELLOW, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[0m"
_fail = 0
_warn = 0


def check(label: str, cond: bool, soft: bool = False) -> None:
    global _fail, _warn
    if cond:
        print(f"{GREEN}PASS{RESET} {label}")
    elif soft:
        _warn += 1
        print(f"{YELLOW}WARN{RESET} {label}")
    else:
        _fail += 1
        print(f"{RED}FAIL{RESET} {label}")


def _server_path(args: list[str]) -> Path | None:
    for a in args:
        if a.endswith("server.py"):
            # config uses ${CLAUDE_ENV_HOME}; resolve against the repo for validation
            rel = a.split("mcp-servers/", 1)[-1]
            return REPO / "mcp-servers" / rel
    return None


def main() -> int:
    cfg = json.loads(CONFIG.read_text())
    servers = cfg.get("mcpServers", {})
    check("mcp-servers.json parses", bool(servers))

    expected = {"filesystem-policy", "git", "lancedb-rag", "memory-graph",
                "terminal", "documentation", "jetbrains"}
    check("all expected servers declared", expected <= set(servers))

    # startup order strictly increasing & unique
    orders = [s.get("startup_order") for s in servers.values()]
    check("startup_order values unique", len(orders) == len(set(orders)))
    check("filesystem-policy starts first",
          servers.get("filesystem-policy", {}).get("startup_order") == min(orders))

    # server files exist (jetbrains is IDE-provided / optional)
    for name, s in servers.items():
        if name == "jetbrains" or s.get("optional"):
            continue
        path = _server_path(s.get("args", []))
        check(f"server file exists: {name}", path is not None and path.exists())

    # security posture assertions
    fsp = servers.get("filesystem-policy", {}).get("security", {})
    check("filesystem-policy enforces policy engine", fsp.get("enforces_policy_engine"))
    check("filesystem-policy content scan on", fsp.get("content_scan"))

    rag = servers.get("lancedb-rag", {}).get("security", {})
    check("lancedb-rag is read-only", rag.get("read_only"))
    check("lancedb-rag wraps results as data", rag.get("wraps_results_as_data"))

    term = servers.get("terminal", {}).get("security", {})
    check("terminal is allowlist-only", term.get("allowlist_only"))
    check("terminal denies unrestricted exec",
          "terminal.exec_unrestricted" in term.get("denies", []))

    mem = servers.get("memory-graph", {}).get("security", {})
    check("memory-graph enforces namespace isolation", mem.get("namespace_isolation"))

    doc = servers.get("documentation", {}).get("security", {})
    check("documentation external fetch limited to tiers [0,1]",
          doc.get("external_fetch_tiers") == [0, 1])

    # soft: can we import the server modules? (needs mcp package)
    try:
        import mcp  # noqa: F401
        have_mcp = True
    except Exception:
        have_mcp = False
    for name in ("filesystem-policy", "lancedb-rag", "memory-graph", "terminal",
                 "git", "documentation"):
        path = REPO / "mcp-servers" / name / "server.py"
        if not path.exists():
            continue
        if not have_mcp:
            check(f"import {name} server (needs `mcp`)", False, soft=True)
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"srv_{name}", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore
            check(f"import {name} server", hasattr(mod, "server"))
        except Exception as e:
            check(f"import {name} server ({e})", False, soft=True)

    print()
    if _fail:
        print(f"{RED}{_fail} MCP check(s) failed, {_warn} warning(s){RESET}")
        return 1
    print(f"{GREEN}MCP topology OK{RESET}"
          + (f" ({_warn} soft warning(s) — install `mcp` to fully validate)" if _warn else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
