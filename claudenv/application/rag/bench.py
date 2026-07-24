"""
claude-env :: Application - RAG Service - Evaluation Harness

Purpose:
    Measure retrieval quality on YOUR repo so model swaps and re-indexes are
    decisions, not guesses. A golden-query file in the repo defines queries
    and the file globs a good answer must come from; the harness reports
    per-query hit@k and aggregate recall@k and MRR. Run it before and after
    changing config/rag.yaml and compare.

Golden file: <repo>/.claude/rag-eval.yaml
    queries:
      - query: "how is jwt validated"
        expect_paths: ["src/auth/**"]
      - query: "retry policy for outbound http"
        expect_paths: ["src/http/retry.py", "docs/adr/00*-retry*"]
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

import yaml

from claudenv.domain.value_objects import GlobPattern

logger = logging.getLogger(__name__)


class RagBench:
    """Evaluate RAG retrieval quality against golden queries.

    Wraps :func:`run_bench` so callers can construct a bench once
    (bound to a repo/branch) and run multiple batches without rebuilding the
    underlying :class:`RagService` each time.
    """

    def __init__(self, repo: str, branch: str = "main", top_n: int = 8) -> None:
        self.repo = repo
        self.branch = branch
        self.top_n = top_n
        self._service = None

    @property
    def service(self):
        """Lazily-built repo-scoped RagService (constructed on first use)."""
        if self._service is None:
            self._service = _build_service(self.repo, self.branch)
        return self._service

    def run(self, queries: list[dict], top_n: int | None = None) -> dict:
        """Run the bench against ``queries`` and return a report dict."""
        return run_bench(self.repo, self.branch, queries, top_n or self.top_n)

    def load_golden(self, repo_root: Path) -> list[dict]:
        """Load golden queries from ``<repo_root>/.claude/rag-eval.yaml``."""
        return load_golden(Path(repo_root))

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


def _build_service(repo: str, branch: str):
    """Construct a repo-scoped RagService bound to ``repo``/``branch``.

    Mirrors ``claudenv.cli._rag_service``: builds the LanceDB store directly
    with the correct signature rather than relying on the container's
    default-repo singleton, so indexing and retrieval share a consistent repo
    key.
    """
    from claudenv.adapters.vector.lancedb import LanceDbRagRetriever, LanceDbVectorStore
    from claudenv.application.rag import RagIndexer, RagService
    from claudenv.di import get_container
    from claudenv.domain.rag import BranchName, RepoSlug
    from claudenv.ports import (
        IEmbeddingProvider,
        ILanceDBConfig,
        IRagBookkeeping,
        IRAGConfig,
        IReranker,
    )

    container = get_container()
    lancedb_config = container.get(ILanceDBConfig)
    rag_config = container.get(IRAGConfig)
    rcfg = rag_config.get_rag_config()
    store = LanceDbVectorStore(lancedb_config.get_lancedb_path(), rcfg.embedding_dim)
    embedder = container.get(IEmbeddingProvider)
    reranker = container.get(IReranker)
    bk = container.get(IRagBookkeeping)
    indexer = RagIndexer(
        RepoSlug.from_string(repo), BranchName.from_string(branch),
        store, bk, embedder, rcfg,
    )
    retriever = LanceDbRagRetriever(store, embedder, reranker)
    return RagService(indexer, retriever, embedder, reranker, bookkeeping=bk)


def run_bench(repo: str, branch: str, queries: list[dict], top_n: int) -> dict:
    from claudenv.domain.rag import BranchName, RepoSlug

    service = _build_service(repo, branch)
    slug = RepoSlug.from_string(repo)
    bname = BranchName.from_string(branch)
    results, hits = [], 0
    for q in queries:
        text, globs = q["query"], q.get("expect_paths", [])
        t0 = time.perf_counter()
        returned = service.search(slug, bname, text, top_k=top_n)
        ms = int((time.perf_counter() - t0) * 1000)
        paths = [r.chunk.file_path for r in returned]
        compiled = [GlobPattern(g) for g in globs]
        rank = next((i + 1 for i, p in enumerate(paths)
                     if any(g.matches(p) for g in compiled)), None)
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
