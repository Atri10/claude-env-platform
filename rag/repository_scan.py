#!/usr/bin/env python3
"""rag/repository_scan.py -- dry-run: list allowed vs blocked files. No model load.
Usage: python rag/repository_scan.py /path/to/repo"""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rag.indexers.indexer import Indexer

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: repository_scan.py <repo_root>"); sys.exit(2)
    r = Indexer(sys.argv[1]).scan()
    print(f"repo={r['repo']} tier={r['tier']} rag_enabled={r['rag_enabled']}")
    print(f"candidates={r['candidates']} allowed={len(r['allowed'])} blocked={len(r['blocked'])}")
    print("\nBLOCKED files:")
    for f in r['blocked']: print("  ", f)
