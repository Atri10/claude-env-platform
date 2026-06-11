#!/usr/bin/env python3
"""rag/branch_index.py -- index a specific branch (checks it out, indexes, restores).
Usage: python rag/branch_index.py /path/to/repo feature/foo"""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rag.indexers.indexer import Indexer

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: branch_index.py <repo_root> <branch>"); sys.exit(2)
    print(json.dumps(Indexer(sys.argv[1]).index_branch(sys.argv[2]), indent=2))
