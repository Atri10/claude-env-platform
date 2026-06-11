#!/usr/bin/env python3
"""rag/incremental_index.py -- re-index changed files (called by git post-commit).
Usage: python rag/incremental_index.py /path/to/repo file1 file2 ...
       python rag/incremental_index.py /path/to/repo --since HEAD~1"""
import sys, json, subprocess
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rag.indexers.indexer import Indexer

if __name__ == "__main__":
    repo = sys.argv[1]
    if "--since" in sys.argv:
        ref = sys.argv[sys.argv.index("--since")+1]
        out = subprocess.run(["git","-C",repo,"diff","--name-only",ref,"HEAD"],
                             capture_output=True,text=True).stdout
        changed = [f for f in out.splitlines() if f]
    else:
        changed = sys.argv[2:]
    print(json.dumps(Indexer(repo).incremental(changed), indent=2))
