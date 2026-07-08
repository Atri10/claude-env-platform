#!/usr/bin/env python3
"""
claude-env :: nightly repo analyst
File: agents/analysts/nightly_analyst.py
Purpose:
    Background analysis that compounds: every night (launchd/systemd via
    scripts/nightly_memory.sh) it inspects a repo and produces a morning
    digest, and writes the summary into the memory graph so the next Claude
    Code session starts already knowing the repo's current health.

Checks (all offline, no model required):
    * doc drift          — broken doc->code references, stale docs (rag/doc_drift.py)
    * TODO/FIXME aging   — oldest open TODO/FIXME markers via git blame
    * dead Python symbols— top-level defs never referenced outside their file
    * size hotspots      — largest source files (review/refactor candidates)

Output:
    $CLAUDE_ENV_HOME/logs/digests/<repo>-<date>.md   (the digest)
    one memory node (episodic/investigation) per run  (the recall hook)

Usage:
    python agents/analysts/nightly_analyst.py /path/to/repo
    python agents/analysts/nightly_analyst.py /path/to/repo --no-memory
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from rag.doc_drift import scan as doc_drift_scan   # noqa: E402
from lib.logging_setup import get_logger           # noqa: E402

_log = get_logger("agents")

HOME = Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))
_TODO = re.compile(r"(?i)\b(TODO|FIXME|HACK|XXX)\b[:\s](.{0,80})")
MAX_BLAME = 150
_SOURCE_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".java", ".rb", ".rs"}


def _git(root: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(root), *args],
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def _files(root: Path) -> list[str]:
    return [f for f in _git(root, "ls-files").splitlines() if f]


def todo_aging(root: Path, files: list[str]) -> list[dict]:
    """Oldest TODO/FIXME markers with blame dates (capped for speed)."""
    found = []
    for f in files:
        if Path(f).suffix not in _SOURCE_EXT:
            continue
        try:
            for i, line in enumerate((root / f).read_text(errors="ignore")
                                     .splitlines(), 1):
                m = _TODO.search(line)
                if m:
                    found.append({"file": f, "line": i, "kind": m.group(1).upper(),
                                  "text": m.group(2).strip()})
        except Exception:
            continue
    found = found[:MAX_BLAME]
    for t in found:
        blame = _git(root, "blame", "-L", f"{t['line']},{t['line']}",
                     "--porcelain", t["file"])
        m = re.search(r"author-time (\d+)", blame)
        if m:
            age = (datetime.now(timezone.utc)
                   - datetime.fromtimestamp(int(m.group(1)), tz=timezone.utc)).days
            t["age_days"] = age
    found.sort(key=lambda t: -(t.get("age_days") or 0))
    return found


def dead_python_symbols(root: Path, files: list[str], cap: int = 400) -> list[dict]:
    """Top-level functions/classes never referenced outside their own file."""
    py = [f for f in files if f.endswith(".py")][:cap]
    defs: list[tuple[str, str]] = []  # (file, symbol)
    blobs: dict[str, str] = {}
    for f in py:
        try:
            text = (root / f).read_text(errors="ignore")
            blobs[f] = text
            tree = ast.parse(text)
        except Exception:
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = node.name
                if not name.startswith("_") and name not in ("main",):
                    defs.append((f, name))
    dead = []
    for f, name in defs:
        pattern = re.compile(rf"\b{re.escape(name)}\b")
        if not any(pattern.search(text) for other, text in blobs.items()
                   if other != f):
            dead.append({"file": f, "symbol": name})
    return dead[:40]


def size_hotspots(root: Path, files: list[str], top: int = 10) -> list[dict]:
    sized = []
    for f in files:
        if Path(f).suffix in _SOURCE_EXT:
            try:
                sized.append({"file": f,
                              "lines": sum(1 for _ in (root / f).open(errors="ignore"))})
            except Exception:
                # unreadable/vanished file — skip it in the hotspot ranking.
                _log.debug("could not size file %s for hotspot report", f,
                           exc_info=True)
    sized.sort(key=lambda x: -x["lines"])
    return sized[:top]


def render(repo: str, date: str, drift: dict, todos: list[dict],
           dead: list[dict], hotspots: list[dict]) -> str:
    L = [f"# {repo} — nightly digest {date}", ""]
    L += [f"## Doc drift",
          f"- broken doc→code references: {len(drift['broken_references'])}",
          f"- stale docs (code newer): {len(drift['stale_docs'])}"]
    for b in drift["broken_references"][:10]:
        L.append(f"  - `{b['doc']}` → `{b['ref']}` (missing)")
    for s in drift["stale_docs"][:10]:
        L.append(f"  - `{s['doc']}` → `{s['ref']}` "
                 f"(code newer by {s['code_newer_by_days']}d)")
    L += ["", f"## Oldest TODO/FIXME markers ({len(todos)})"]
    for t in todos[:12]:
        age = f"{t['age_days']}d" if t.get("age_days") is not None else "?"
        L.append(f"  - [{age:>5s}] {t['kind']} {t['file']}:{t['line']} — {t['text']}")
    L += ["", f"## Possibly-dead Python symbols ({len(dead)})",
          "  (top-level defs with no reference outside their own file — verify before removing)"]
    for d in dead[:15]:
        L.append(f"  - `{d['symbol']}` in {d['file']}")
    L += ["", "## Largest source files"]
    for h in hotspots:
        L.append(f"  - {h['lines']:>6d} lines  {h['file']}")
    L.append("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="Nightly repo analyst digest")
    ap.add_argument("repo_root")
    ap.add_argument("--no-memory", action="store_true",
                    help="write the digest file only, skip the memory node")
    args = ap.parse_args()

    root = Path(args.repo_root).resolve()
    repo = root.name
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    files = _files(root)
    if not files:
        print(f"{root} has no committed files (not a git repo?) — nothing to analyze")
        return 0

    drift = doc_drift_scan(root)
    todos = todo_aging(root, files)
    dead = dead_python_symbols(root, files)
    hotspots = size_hotspots(root, files)

    digest = render(repo, date, drift, todos, dead, hotspots)
    out_dir = HOME / "logs" / "digests"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{repo}-{date}.md"
    out.write_text(digest)
    print(f"digest written -> {out}")

    if not args.no_memory:
        try:
            from memory.memory_manager import MemoryManager
            mgr = MemoryManager(namespace=f"proj-{repo}",
                                session_id="nightly-analyst", actor="nightly-analyst")
            mgr.add_node(
                "episodic", "investigation", f"nightly digest {date}",
                {"summary": {
                    "broken_doc_refs": len(drift["broken_references"]),
                    "stale_docs": len(drift["stale_docs"]),
                    "open_todos": len(todos),
                    "oldest_todo_days": todos[0].get("age_days") if todos else 0,
                    "possibly_dead_symbols": len(dead)},
                 "digest_path": str(out)},
                repo=repo, confidence=0.8)
            print("memory node written (episodic/investigation)")
        except Exception as exc:
            print(f"WARN: memory write skipped: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
