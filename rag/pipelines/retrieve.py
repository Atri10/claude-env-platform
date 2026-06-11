"""
claude-env :: retrieval pipeline
File: rag/pipelines/retrieve.py
Purpose:
    End-to-end query: embed query -> hybrid search (LanceDB) -> rerank -> top-N.
    Emits a retrieval audit event and a latency metric. Wraps each returned
    chunk in injection-safe delimiters for the agent.

Usage:
    from rag.pipelines.retrieve import Retriever
    r = Retriever(repo="payments", branch="main", session_id="sess-1")
    hits = r.query("how is jwt validated", top_n=8)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from rag.embeddings.llama_embedder import LlamaEmbedder      # noqa: E402
from rag.retrievers.lance_store import LanceStore            # noqa: E402
from rag.rerankers.cross_encoder import CrossEncoderReranker  # noqa: E402
from audit.audit_logger import AuditLogger                   # noqa: E402
from observability.collectors import record_latency, record_retrieval_quality  # noqa: E402

CHUNK_OPEN = "<retrieved_context source=\"{path}\" lines=\"{a}-{b}\">"
CHUNK_CLOSE = "</retrieved_context>"


class Retriever:
    def __init__(self, repo: str, branch: str = "main",
                 session_id: str = "adhoc", actor: str = "research-agent"):
        self.repo, self.branch = repo, branch
        self.embedder = LlamaEmbedder()
        self.store = LanceStore(dim=self.embedder.dim)
        self.reranker = CrossEncoderReranker()
        self.audit = AuditLogger(session_id, actor=actor, repo=repo)

    def query(self, text: str, top_k: int = 40, top_n: int = 8) -> list[dict]:
        t0 = time.perf_counter()
        qv = self.embedder.embed_query(text)
        candidates = self.store.search(self.repo, self.branch, qv, text, top_k)
        t_search = (time.perf_counter() - t0) * 1000
        record_latency("rag.retrieve", "hybrid_search", int(t_search), self.repo)

        t1 = time.perf_counter()
        ranked = self.reranker.rerank(text, candidates, top_n=top_n)
        t_rerank = (time.perf_counter() - t1) * 1000
        record_latency("rag.rerank", "cross_encoder", int(t_rerank), self.repo)

        scores = [c.get("_distance") or c.get("rerank_score") or 0.0 for c in ranked]
        self.audit.retrieval(
            repo=self.repo, branch=self.branch, query=text, top_k=top_k,
            returned=len(ranked),
            max_score=max(scores) if scores else None,
            min_score=min(scores) if scores else None,
            reranked=self.reranker.ok,
            duration_ms=int(t_search + t_rerank))
        record_retrieval_quality(
            self.repo, text,
            top1=ranked[0].get("rerank_score") if ranked else None,
            mean_top_k=(sum(scores) / len(scores)) if scores else None)

        # wrap for injection safety
        for c in ranked:
            c["wrapped"] = (CHUNK_OPEN.format(path=c.get("file_path", "?"),
                                              a=c.get("start_line", 0),
                                              b=c.get("end_line", 0))
                            + "\n" + c.get("text", "") + "\n" + CHUNK_CLOSE)
        return ranked


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("repo"); ap.add_argument("query")
    ap.add_argument("--branch", default="main"); ap.add_argument("-n", type=int, default=8)
    a = ap.parse_args()
    for h in Retriever(a.repo, a.branch).query(a.query, top_n=a.n):
        print(f"[{h.get('rerank_score', 0):.2f}] {h['file_path']}:{h['start_line']}-{h['end_line']}")
