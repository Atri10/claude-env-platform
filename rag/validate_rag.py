#!/usr/bin/env python3
"""rag/validate_rag.py -- health check the RAG subsystem for a repo.
Usage: python rag/validate_rag.py /path/to/repo "test query" """
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.db import get_db

def main(repo_root, query):
    from rag.indexers.indexer import Indexer
    idx = Indexer(repo_root); repo = idx.repo
    db = get_db()
    state = db.query_one("SELECT * FROM rag_index_state WHERE repo=? ORDER BY updated_at DESC LIMIT 1",(repo,))
    checks = []
    checks.append(("index_state_present", state is not None))
    if state:
        checks.append(("chunk_count_positive", state["chunk_count"] > 0))
    try:
        from rag.pipelines.retrieve import Retriever
        hits = Retriever(repo, state["branch"] if state else "main").query(query, top_n=5)
        checks.append(("retrieval_returns_hits", len(hits) > 0))
    except Exception as e:
        checks.append((f"retrieval_error:{e}", False))
    ok = all(v for _, v in checks)
    for name, v in checks: print(f"  [{'PASS' if v else 'FAIL'}] {name}")
    print("RAG:", "OK" if ok else "DEGRADED"); sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv)>2 else "test")
