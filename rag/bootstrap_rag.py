#!/usr/bin/env python3
"""rag/bootstrap_rag.py -- full index of a repository.
Usage: python rag/bootstrap_rag.py /path/to/repo"""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rag.indexers.indexer import Indexer

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: bootstrap_rag.py <repo_root>"); sys.exit(2)
    print(json.dumps(Indexer(sys.argv[1]).full_index(), indent=2))
