#!/usr/bin/env python3
"""
claude-env :: policy simulation & drift diff
File: security/policy_sim.py
Purpose:
    Make policy changes safe to roll out.

    simulate — evaluate a CANDIDATE repo policy against the repo's real file
    tree, side by side with the CURRENT policy, and report exactly which files
    become newly blocked / newly allowed. No state is touched; the engine is
    stateless.

    diff — structural comparison of two policy YAML files (tier, allow/deny
    sets, content-scan config) for drift detection against a team baseline.

Usage:
    python security/policy_sim.py simulate /repo --candidate new-policy.yaml
    python security/policy_sim.py simulate /repo --candidate new.yaml --format json
    python security/policy_sim.py diff current.yaml baseline.yaml
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import yaml                                            # noqa: E402
from security.policy_engine import PolicyEngine        # noqa: E402

GLOBAL_POLICY = "~/.claude-env/config/global-policy.yaml"


def _repo_files(root: Path) -> list[str]:
    """Committed files when it's a git repo, else a bounded rglob."""
    r = subprocess.run(["git", "-C", str(root), "ls-files"],
                       capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        return [f for f in r.stdout.splitlines() if f]
    out = []
    for p in root.rglob("*"):
        if p.is_file() and ".git" not in p.parts:
            out.append(str(p.relative_to(root)))
            if len(out) >= 20000:
                break
    return out


def _engine_for_doc(repo_root: Path, repo_doc: dict) -> PolicyEngine:
    """Build an engine from an in-memory candidate doc (same path load() takes)."""
    gdoc = PolicyEngine._read_yaml(GLOBAL_POLICY)
    slug = repo_doc.get("repo", repo_root.name)
    return PolicyEngine(repo=PolicyEngine._compile(repo_doc, gdoc, slug),
                        glob=PolicyEngine._compile(gdoc, gdoc, slug))


def simulate(repo_root: Path, candidate_path: Path) -> dict:
    current = PolicyEngine.load(repo_root, GLOBAL_POLICY)
    cdoc = yaml.safe_load(candidate_path.read_text()) or {}
    candidate = _engine_for_doc(repo_root, cdoc)

    files = _repo_files(repo_root)
    newly_blocked, newly_allowed, changed_rule = [], [], []
    counts = {"files": len(files), "blocked_now": 0, "blocked_candidate": 0}

    for f in files:
        cur = current.evaluate_path(f)
        cand = candidate.evaluate_path(f)
        if cur.action == "block":
            counts["blocked_now"] += 1
        if cand.action == "block":
            counts["blocked_candidate"] += 1
        if cur.action != cand.action:
            entry = {"path": f, "current": cur.action, "candidate": cand.action,
                     "candidate_rule": cand.rule or cand.reason}
            (newly_blocked if cand.action == "block" else newly_allowed).append(entry)
        elif cur.action == "block" and (cur.rule != cand.rule):
            changed_rule.append({"path": f, "rule_now": cur.rule,
                                 "rule_candidate": cand.rule})

    return {"repo": str(repo_root), "candidate": str(candidate_path),
            "tier_current": current.repo.tier, "tier_candidate": candidate.repo.tier,
            "counts": counts,
            "newly_blocked": newly_blocked, "newly_allowed": newly_allowed,
            "blocked_rule_changes": changed_rule}


def diff_policies(a_path: Path, b_path: Path) -> dict:
    """Structural drift between two policy files (e.g. repo vs team baseline)."""
    a = yaml.safe_load(a_path.read_text()) or {}
    b = yaml.safe_load(b_path.read_text()) or {}

    def section(doc: dict, *keys) -> set:
        cur = doc
        for k in keys:
            cur = (cur or {}).get(k, {})
        if isinstance(cur, list):
            return {json.dumps(x, sort_keys=True) if isinstance(x, dict) else str(x)
                    for x in cur}
        return set()

    out = {"a": str(a_path), "b": str(b_path), "drift": []}
    if a.get("tier") != b.get("tier"):
        out["drift"].append({"field": "tier", "a": a.get("tier"), "b": b.get("tier")})
    for label, keys in [("deny.paths", ("deny", "paths")),
                        ("deny.extensions", ("deny", "extensions")),
                        ("deny.regex", ("deny", "regex")),
                        ("allow.paths", ("allow", "paths")),
                        ("allow.extensions", ("allow", "extensions")),
                        ("content_scan.patterns", ("content_scan", "patterns"))]:
        sa, sb = section(a, *keys), section(b, *keys)
        if sa != sb:
            out["drift"].append({"field": label,
                                 "only_in_a": sorted(sa - sb),
                                 "only_in_b": sorted(sb - sa)})
    csa = (a.get("content_scan") or {}).get("on_match")
    csb = (b.get("content_scan") or {}).get("on_match")
    if csa != csb:
        out["drift"].append({"field": "content_scan.on_match", "a": csa, "b": csb})
    out["in_sync"] = not out["drift"]
    return out


def _print_sim(r: dict) -> None:
    c = r["counts"]
    print(f"# policy simulation — {r['repo']}")
    print(f"tier: {r['tier_current']} -> {r['tier_candidate']}   "
          f"files: {c['files']}   blocked: {c['blocked_now']} -> {c['blocked_candidate']}\n")
    for title, rows in [("NEWLY BLOCKED", r["newly_blocked"]),
                        ("NEWLY ALLOWED", r["newly_allowed"])]:
        print(f"{title}: {len(rows)}")
        for e in rows[:50]:
            print(f"  {e['path']}  ({e['candidate_rule']})")
        if len(rows) > 50:
            print(f"  ... and {len(rows) - 50} more")
        print()
    if r["blocked_rule_changes"]:
        print(f"blocked-by-different-rule: {len(r['blocked_rule_changes'])}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Policy simulation / drift diff")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sim = sub.add_parser("simulate")
    sim.add_argument("repo_root")
    sim.add_argument("--candidate", required=True)
    sim.add_argument("--format", choices=["text", "json"], default="text")
    dif = sub.add_parser("diff")
    dif.add_argument("policy_a")
    dif.add_argument("policy_b")
    dif.add_argument("--format", choices=["text", "json"], default="text")
    args = ap.parse_args()

    if args.cmd == "simulate":
        r = simulate(Path(args.repo_root).resolve(), Path(args.candidate))
        if args.format == "json":
            print(json.dumps(r, indent=2))
        else:
            _print_sim(r)
        return 0

    r = diff_policies(Path(args.policy_a), Path(args.policy_b))
    if args.format == "json":
        print(json.dumps(r, indent=2))
    elif r["in_sync"]:
        print("policies are structurally identical")
    else:
        print(f"drift between {r['a']} and {r['b']}:")
        for d in r["drift"]:
            print(f"  {d['field']}:")
            for side in ("a", "b", "only_in_a", "only_in_b"):
                if d.get(side):
                    print(f"    {side}: {d[side]}")
    return 0 if (args.cmd == "simulate" or r["in_sync"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
