#!/usr/bin/env python3
"""
claude-env :: RAG evaluation harness
File: rag/rag_bench.py
Purpose:
    Measure retrieval quality on YOUR repo so model swaps and re-indexes are
    decisions, not guesses. A golden-query file in the repo defines queries and
    the file globs a good answer must come from; the harness reports per-query
    hit@k and aggregate recall. Run it before and after changing
    config/rag.yaml and compare.

Golden file: <repo>/.claude/rag-eval.yaml
    queries:
      - query: "how is jwt validated"
        expect_paths: ["src/auth/**"]
      - query: "retry policy for outbound http"
        expect_paths: ["src/http/retry.py", "docs/adr/00*-retry*"]

Usage:
    python rag/rag_bench.py /path/to/repo                  # run benchmark
    python rag/rag_bench.py /path/to/repo --top-n 10
    python rag/rag_bench.py /path/to/repo --create-template
    python rag/rag_bench.py /path/to/repo --format json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import yaml                                          # noqa: E402
from security.policy_engine import _pmatch           # noqa: E402

TEMPLATE = """\
# claude-env RAG golden queries — used by `claude-env rag-bench`.
# Each query lists the path globs a correct retrieval should surface.
queries:
  - query: "example: how is authentication validated"
    expect_paths: ["src/auth/**"]
  - query: "example: where is the database schema defined"
    expect_paths: ["sql/**", "migrations/**"]
"""


def load_golden(repo_root: Path) -> list[dict]:
    p = repo_root / ".claude" / "rag-eval.yaml"
    if not p.exists():
        raise SystemExit(
            f"no golden file at {p} — create one with --create-template")
    doc = yaml.safe_load(p.read_text()) or {}
    queries = doc.get("queries") or []
    if not queries:
        raise SystemExit(f"{p} has no queries")
    return queries


def run_bench(repo: str, branch: str, queries: list[dict], top_n: int) -> dict:
    from rag.pipelines.retrieve import Retriever
    r = Retriever(repo=repo, branch=branch, session_id="rag-bench", actor="rag-bench")
    results, hits = [], 0
    for q in queries:
        text, globs = q["query"], q.get("expect_paths", [])
        t0 = time.perf_counter()
        returned = r.query(text, top_n=top_n)
        ms = int((time.perf_counter() - t0) * 1000)
        paths = [c.get("file_path", "") for c in returned]
        rank = next((i + 1 for i, p in enumerate(paths)
                     if any(_pmatch(p, g) for g in globs)), None)
        hit = rank is not None
        hits += hit
        results.append({"query": text, "hit": hit, "rank": rank,
                        "latency_ms": ms, "returned": paths[:top_n]})
    return {"repo": repo, "branch": branch, "top_n": top_n,
            "queries": len(queries), "hits": hits,
            "recall_at_k": round(hits / len(queries), 3),
            "mrr": round(sum(1 / r["rank"] for r in results if r["rank"])
                         / len(queries), 3),
            "results": results}


def main() -> int:
    ap = argparse.ArgumentParser(description="Benchmark retrieval against golden queries")
    ap.add_argument("repo_root")
    ap.add_argument("--repo", help="index name (defaults to directory name)")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--top-n", type=int, default=8)
    ap.add_argument("--create-template", action="store_true")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    args = ap.parse_args()

    root = Path(args.repo_root).resolve()
    if args.create_template:
        p = root / ".claude" / "rag-eval.yaml"
        if p.exists():
            print(f"{p} already exists — not overwriting")
            return 1
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(TEMPLATE)
        print(f"template written -> {p}\nEdit the queries, then run rag-bench again.")
        return 0

    queries = load_golden(root)
    try:
        result = run_bench(args.repo or root.name, args.branch, queries, args.top_n)
    except Exception as exc:
        print(f"benchmark needs a configured embedding model and an indexed repo: {exc}")
        return 2

    if args.format == "json":
        print(json.dumps(result, indent=2))
        return 0
    print(f"# rag-bench — {result['repo']}@{result['branch']}  "
          f"recall@{result['top_n']}: {result['recall_at_k']}  MRR: {result['mrr']}")
    for r in result["results"]:
        mark = f"rank {r['rank']}" if r["hit"] else "MISS"
        print(f"  [{mark:>7s}] ({r['latency_ms']:>4d}ms) {r['query'][:70]}")
        if not r["hit"]:
            for p in r["returned"][:3]:
                print(f"            got: {p}")
    return 0 if result["recall_at_k"] == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
