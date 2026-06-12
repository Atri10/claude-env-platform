#!/usr/bin/env python3
"""
claude-env :: test-impact analysis
File: rag/test_impact.py
Purpose:
    Given a set of changed files, suggest the minimal set of test files (and
    runnable commands) likely to exercise them, so agents and humans run the
    right tests instead of the whole suite.

Heuristics (deliberately simple, language-aware, offline):
    1. name convention   — test_<stem>*, <stem>_test.*, <stem>.test.*, <stem>.spec.*
    2. import reference  — test files whose text references the changed module
                           (Python: module path; JS/TS: relative import of stem)
    3. self              — a changed test file is always selected

Usage:
    python rag/test_impact.py /repo --since HEAD~1
    python rag/test_impact.py /repo --files src/auth/jwt.py src/api/orders.py
    python rag/test_impact.py /repo --since HEAD~3 --format json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

_TEST_HINTS = re.compile(r"(^|/)(tests?|__tests__|spec)s?(/|$)|"
                         r"(^|/)test_[^/]+$|_test\.[a-z]+$|\.(test|spec)\.[a-z]+$")
_SOURCE_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".java", ".rb", ".rs"}


def _git(root: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(root), *args],
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def _all_files(root: Path) -> list[str]:
    return [f for f in _git(root, "ls-files").splitlines() if f]


def is_test_file(path: str) -> bool:
    return bool(_TEST_HINTS.search(path))


def _name_matches(stem: str, test_path: str) -> bool:
    tname = Path(test_path).name
    return (tname.startswith(f"test_{stem}") or tname.startswith(f"{stem}_test")
            or tname.startswith(f"{stem}.test.") or tname.startswith(f"{stem}.spec."))


def _reference_matches(changed: str, test_path: str, root: Path) -> bool:
    """Does the test file textually reference the changed module?"""
    stem = Path(changed).stem
    if len(stem) < 3:
        return False
    try:
        text = (root / test_path).read_text(errors="ignore")
    except Exception:
        return False
    if changed.endswith(".py"):
        module = changed[:-3].replace("/", ".")
        return bool(re.search(rf"(?m)^\s*(from|import)\s+{re.escape(module)}\b", text)
                    or re.search(rf"(?m)^\s*from\s+\S*\b{re.escape(stem)}\b\s+import", text))
    # JS/TS/Go/etc: a require/import mentioning the stem
    return bool(re.search(rf"""(import|require|from)\s*\(?["'][^"']*\b{re.escape(stem)}\b""", text))


def analyze(root: Path, changed: list[str]) -> dict:
    files = _all_files(root)
    test_files = [f for f in files if is_test_file(f)
                  and Path(f).suffix in _SOURCE_EXT]
    selected: dict[str, list[str]] = {}

    for ch in changed:
        if is_test_file(ch):
            selected.setdefault(ch, []).append("changed test")
            continue
        if Path(ch).suffix not in _SOURCE_EXT:
            continue
        stem = Path(ch).stem
        for t in test_files:
            why = None
            if _name_matches(stem, t):
                why = f"name match for {ch}"
            elif _reference_matches(ch, t, root):
                why = f"references {ch}"
            if why:
                selected.setdefault(t, []).append(why)

    commands = []
    py = sorted(t for t in selected if t.endswith(".py"))
    js = sorted(t for t in selected if Path(t).suffix in
                (".ts", ".tsx", ".js", ".jsx"))
    go_pkgs = sorted({str(Path(t).parent) for t in selected if t.endswith(".go")})
    if py:
        commands.append("pytest " + " ".join(py))
    if js:
        commands.append("npx jest " + " ".join(js))
    for p in go_pkgs:
        commands.append(f"go test ./{p}/...")

    return {"changed": changed,
            "tests": [{"file": t, "reasons": sorted(set(r))}
                      for t, r in sorted(selected.items())],
            "commands": commands,
            "note": "" if selected else
            "no related tests found — consider running the full suite"}


def main() -> int:
    ap = argparse.ArgumentParser(description="Suggest tests for changed files")
    ap.add_argument("repo_root")
    ap.add_argument("--since", help="git revision, e.g. HEAD~1")
    ap.add_argument("--files", nargs="*", help="explicit changed files")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    args = ap.parse_args()

    root = Path(args.repo_root).resolve()
    changed = list(args.files or [])
    if args.since:
        changed += [f for f in _git(root, "diff", "--name-only",
                                    args.since).splitlines() if f]
    if not changed:
        print("nothing changed (give --since or --files)")
        return 0
    result = analyze(root, sorted(set(changed)))
    if args.format == "json":
        print(json.dumps(result, indent=2))
        return 0
    print(f"# test impact — {len(result['changed'])} changed file(s)")
    for t in result["tests"]:
        print(f"  {t['file']}")
        for r in t["reasons"]:
            print(f"      - {r}")
    if result["commands"]:
        print("\nsuggested commands:")
        for c in result["commands"]:
            print(f"  {c}")
    if result["note"]:
        print(result["note"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
