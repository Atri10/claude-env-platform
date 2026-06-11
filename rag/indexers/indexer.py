"""
claude-env :: repository indexing
File: rag/indexers/indexer.py
Purpose:
    Walk a repo (committed files only), enforce policy + RAG scope, chunk,
    embed, and upsert into LanceDB. Tracks per-file content hashes in
    rag_file_state so incremental indexing only re-embeds changed files.

    This module backs the named entry-point scripts:
      bootstrap_rag.py     -> Indexer.full_index()
      repository_scan.py   -> Indexer.scan() (dry-run: what would be indexed)
      incremental_index.py -> Indexer.incremental()
      branch_index.py      -> Indexer.index_branch()

Policy: each candidate path is run through PolicyEngine.evaluate_path. Blocked
paths are skipped AND logged as policy_violations (decision='block').
RAG scope (rag.index_paths / exclude_paths) further narrows allowed files.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path


def _progress(current: int, total: int, label: str = "", width: int = 40) -> None:
    pct = current / total if total else 1.0
    filled = int(width * pct)
    bar = "#" * filled + "-" * (width - filled)
    sys.stderr.write(f"\r[{bar}] {current}/{total} {label}  ")
    sys.stderr.flush()
    if current >= total:
        sys.stderr.write("\n")
        sys.stderr.flush()

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lib.db import get_db                                    # noqa: E402
from security.policy_engine import PolicyEngine, _pmatch     # noqa: E402
from rag.chunkers.chunkers import chunk_file                 # noqa: E402
from rag.embeddings.llama_embedder import LlamaEmbedder      # noqa: E402
from rag.retrievers.lance_store import LanceStore, table_name  # noqa: E402
from audit.audit_logger import AuditLogger                   # noqa: E402
import hashlib                                                # noqa: E402


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _git(repo_root: str, *args: str) -> str:
    return subprocess.run(["git", "-C", repo_root, *args],
                          capture_output=True, text=True).stdout.strip()


class Indexer:
    def __init__(self, repo_root: str, session_id: str = "indexer"):
        self.root = str(Path(repo_root).resolve())
        self.policy = PolicyEngine.load(self.root)
        self.repo = self.policy.repo.repo
        self.tier = self.policy.repo.tier
        self.embedder = None      # lazy: avoid loading model for dry-run scan
        self.store = None
        self.db = get_db()
        self.audit = AuditLogger(session_id, actor="indexer", repo=self.repo, tier=self.tier)
        # load rag scope from repo policy yaml directly
        import yaml
        pol = Path(self.root) / ".claude" / "repo-policy.yaml"
        doc = yaml.safe_load(pol.read_text()) if pol.exists() else {}
        rag = (doc.get("rag") or {})
        self.rag_enabled = rag.get("enabled", self.tier < 3)
        self.index_paths = rag.get("index_paths", ["**"])
        self.exclude_paths = rag.get("exclude_paths", [])
        self.only_committed = rag.get("index_only_committed", True)

    def _lazy(self):
        if self.embedder is None:
            self.embedder = LlamaEmbedder()
            self.store = LanceStore(dim=self.embedder.dim)

    def _candidate_files(self) -> list[str]:
        if self.only_committed:
            out = _git(self.root, "ls-files")
            files = [f for f in out.splitlines() if f]
        else:
            files = [str(p.relative_to(self.root)) for p in Path(self.root).rglob("*")
                     if p.is_file()]
        return files

    def _allowed(self, rel: str) -> bool:
        if not any(_pmatch(rel, g) for g in self.index_paths):
            return False
        if any(_pmatch(rel, g) for g in self.exclude_paths):
            return False
        dec = self.policy.evaluate_path(rel)
        if dec.action == "block":
            self.audit.policy_violation(rel, dec.rule or dec.reason, "block", self.tier)
            return False
        return True

    def scan(self) -> dict:
        """Dry-run: report what WOULD be indexed vs blocked. No model load."""
        cand = self._candidate_files()
        allowed, blocked = [], []
        for rel in cand:
            (allowed if self._allowed(rel) else blocked).append(rel)
        return {"repo": self.repo, "tier": self.tier, "rag_enabled": self.rag_enabled,
                "candidates": len(cand), "allowed": allowed, "blocked": blocked}

    def _index_one(self, branch: str, commit: str, rel: str) -> int:
        abs_path = Path(self.root) / rel
        try:
            text = abs_path.read_text(errors="ignore")
        except Exception:
            return 0
        # content scan (redaction) before indexing
        text, hits = self.policy.scan_content(text)
        if hits and hits[0][1] == -1:
            self.audit.security_event("secret", "high",
                                      f"content blocked in {rel}", source=rel)
            return 0
        chash = hashlib.sha256(text.encode()).hexdigest()
        prior = self.db.query_one(
            "SELECT content_hash FROM rag_file_state WHERE repo=? AND branch=? AND file_path=?",
            (self.repo, branch, rel))
        if prior and prior["content_hash"] == chash:
            return 0   # unchanged
        chunks = chunk_file(rel, text, self.repo, branch, commit)
        if not chunks:
            return 0
        vectors = self.embedder.embed_documents([c.text for c in chunks])
        rows = []
        for c, v in zip(chunks, vectors):
            m = c.as_metadata()
            rows.append({
                "chunk_id": m["chunk_id"], "vector": v, "text": m["text"],
                "repo": m["repo"], "branch": m["branch"], "commit_sha": m["commit_sha"],
                "file_path": m["file_path"], "file_type": m["file_type"],
                "symbol_type": m["symbol_type"], "symbol_name": m["symbol_name"],
                "start_line": m["start_line"], "end_line": m["end_line"],
                "content_hash": m["content_hash"], "tier": self.tier,
            })
        self.store.delete_file(self.repo, branch, rel)  # replace prior chunks
        self.store.upsert(self.repo, branch, rows)
        self.db.execute(
            "INSERT INTO rag_file_state (repo,branch,file_path,content_hash,chunk_count,indexed_at) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(repo,branch,file_path) DO UPDATE SET "
            "content_hash=excluded.content_hash, chunk_count=excluded.chunk_count, "
            "indexed_at=excluded.indexed_at",
            (self.repo, branch, rel, chash, len(rows), _now()))
        return len(rows)

    def full_index(self) -> dict:
        if not self.rag_enabled:
            return {"skipped": True, "reason": "rag disabled for this tier/repo"}
        self._lazy()
        branch = _git(self.root, "rev-parse", "--abbrev-ref", "HEAD") or "main"
        commit = _git(self.root, "rev-parse", "HEAD") or "0"
        t0 = time.perf_counter()
        total_chunks = total_files = skipped = 0
        candidates = [r for r in self._candidate_files() if self._allowed(r)]
        total = len(candidates)
        sys.stderr.write(f"Indexing {self.repo} ({total} files) ...\n")
        for i, rel in enumerate(candidates, 1):
            _progress(i, total, rel[:50])
            n = self._index_one(branch, commit, rel)
            if n:
                total_files += 1
                total_chunks += n
            else:
                skipped += 1
        elapsed = round(time.perf_counter() - t0, 2)
        sys.stderr.write(f"Done: {total_files} indexed, {skipped} unchanged, "
                         f"{total_chunks} chunks in {elapsed}s\n")
        self._record_index_state(branch, commit)
        self.audit.agent_action("indexer", "full_index", target=self.repo,
                                summary=f"{total_files} files / {total_chunks} chunks")
        return {"repo": self.repo, "branch": branch, "files": total_files,
                "chunks": total_chunks, "seconds": elapsed}

    def incremental(self, changed: list[str]) -> dict:
        if not self.rag_enabled:
            return {"skipped": True}
        self._lazy()
        branch = _git(self.root, "rev-parse", "--abbrev-ref", "HEAD") or "main"
        commit = _git(self.root, "rev-parse", "HEAD") or "0"
        files = chunks = 0
        total = len(changed)
        sys.stderr.write(f"Incremental index: {total} changed files ...\n")
        for i, rel in enumerate(changed, 1):
            _progress(i, total, rel[:50])
            if not (Path(self.root) / rel).exists():
                self.store.delete_file(self.repo, branch, rel)
                self.db.execute("DELETE FROM rag_file_state WHERE repo=? AND branch=? AND file_path=?",
                                (self.repo, branch, rel))
                continue
            if self._allowed(rel):
                n = self._index_one(branch, commit, rel)
                if n:
                    files += 1; chunks += n
        self._record_index_state(branch, commit)
        return {"repo": self.repo, "branch": branch, "files": files, "chunks": chunks}

    def index_branch(self, branch: str) -> dict:
        cur = _git(self.root, "rev-parse", "--abbrev-ref", "HEAD")
        if cur != branch:
            _git(self.root, "checkout", branch)
        try:
            return self.full_index()
        finally:
            if cur and cur != branch:
                _git(self.root, "checkout", cur)

    def _record_index_state(self, branch: str, commit: str):
        total = self.store.count(self.repo, branch) if self.store else 0
        self.db.execute(
            "INSERT INTO rag_index_state (repo,branch,table_name,last_commit,chunk_count,embed_model,updated_at) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(repo,branch) DO UPDATE SET last_commit=excluded.last_commit, "
            "chunk_count=excluded.chunk_count, updated_at=excluded.updated_at",
            (self.repo, branch, table_name(self.repo, branch), commit, total,
             "nomic-embed-text-v1.5.Q8_0", _now()))
