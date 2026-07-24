"""
claude-env :: Application - Memory - Session Transcript Ingestor
File: claudenv/application/memory/session_ingestor.py
Purpose:
    Make the memory graph SELF-POPULATING. Claude Code stores every session as
    a JSONL transcript under ~/.claude/projects/<munged-cwd>/<session>.jsonl.
    This ingestor parses new transcripts and writes one episodic `session`
    memory node per session: the task, files touched, tools used, and outcome.
    The next session in that repo can recall what previous sessions did, with
    zero user discipline required.

    It also closes the retrieval feedback loop: if a file that the RAG pipeline
    previously returned (rag_chunk_feedback signal='retrieved') was edited in a
    session, a signal='used' row is recorded -- a heuristic time-window
    correlation that the retriever turns into a ranking boost.

Onboarding scope: this ingestor walks ~/.claude/projects/ directly and does
NOT go through the per-repo native-tool hooks, so it is not narrowed by the
repo-local hook install. It must do its own onboarding check: a transcript is
only ingested if its cwd is inside a repo with a <repo>/.claude/repo-policy.yaml
(no basename fallback). Transcripts from un-onboarded repos are recorded in
session_ingest_state as skipped (so they are not rescanned every night) but
never reach the memory graph.

Privacy: node bodies pass through SecretDetector.redact() before storage; the
transcript itself never leaves disk. Dedupe via session_ingest_state.

Usage:
    python -m claudenv.application.memory.session_ingestor           # ingest all new transcripts
    python -m claudenv.application.memory.session_ingestor --dry-run # report only
    python -m claudenv.application.memory.session_ingestor --transcripts /custom/dir
    (scheduled nightly via scripts/nightly_memory.sh)
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from claudenv.adapters.persistence import SQLiteDatabase, SQLiteMemoryRepository
from claudenv.domain.memory import MemoryType
from claudenv.domain.memory.service.writer import MemoryWriter
from claudenv.domain.security import SecretDetector
from claudenv.ports import IDatabase, IEmbeddingProvider, IMemoryRepository

logger = logging.getLogger(__name__)

DEFAULT_TRANSCRIPTS = Path.home() / ".claude" / "projects"
MIN_EVENTS = 3          # skip trivial sessions
MAX_OUTCOME = 400       # chars of final assistant text stored
USED_WINDOW_HOURS = 24  # retrieval->edit correlation window


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _text_of(content: Any) -> str:
    """Flatten a Claude message content field (str or block list) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _repo_slug(cwd: str, fallback_to_basename: bool = False) -> str | None:
    """Resolve the repo slug for ``cwd`` from its repo-policy.yaml.

    Mirrors the legacy ``lib.repo_policy.repo_slug`` onboarding gate: a
    transcript is only ingested if its cwd holds a ``.claude/repo-policy.yaml``
    with a ``repo:`` field. With ``fallback_to_basename=False`` (the ingestor's
    default) a repo that was never `claude-env register`-ed returns None -- skip,
    not "guess a label and ingest anyway". With ``True`` the directory basename
    is returned, matching the legacy basename fallback.
    """
    if not cwd:
        return None
    policy = Path(cwd) / ".claude" / "repo-policy.yaml"
    if policy.exists():
        try:
            data = yaml.safe_load(policy.read_text()) or {}
            repo = data.get("repo")
            if repo:
                return str(repo)
        except Exception:
            logger.warning("could not resolve repo from cwd", exc_info=True)
    if fallback_to_basename:
        return Path(cwd).resolve().name
    return None


def _parse_transcript(path: Path) -> dict | None:
    """Extract a compact session summary from one JSONL transcript."""
    first_task, outcome, cwd = "", "", ""
    files: set[str] = set()
    edited: set[str] = set()
    tools: dict[str, int] = {}
    first_ts = last_ts = ""
    events = 0

    with path.open(errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                # a single malformed JSONL line shouldn't abort ingesting the
                # whole transcript; skip it (debug -- transcripts can be large).
                logger.debug("skipping unparseable transcript line in %s",
                             path.name, exc_info=True)
                continue
            events += 1
            cwd = rec.get("cwd") or cwd
            ts = rec.get("timestamp") or ""
            if ts:
                first_ts = first_ts or ts
                last_ts = ts
            msg = rec.get("message") or {}
            role = msg.get("role") or rec.get("type")
            content = msg.get("content")
            if role == "user" and not first_task:
                t = _text_of(content).strip()
                if t and not t.startswith(("<", "Caveat:")):
                    first_task = t[:300]
            if role == "assistant":
                t = _text_of(content).strip()
                if t:
                    outcome = t[-MAX_OUTCOME:]
                if isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "tool_use":
                            name = b.get("name", "?")
                            tools[name] = tools.get(name, 0) + 1
                            tin = b.get("input") or {}
                            fp = (tin.get("file_path") or tin.get("path")
                                  or tin.get("notebook_path"))
                            if fp:
                                files.add(str(fp))
                                if name in ("Write", "Edit", "NotebookEdit"):
                                    edited.add(str(fp))

    if events < MIN_EVENTS:
        return None
    return {"task": first_task, "outcome": outcome, "cwd": cwd,
            "files": sorted(files)[:50], "edited": sorted(edited)[:50],
            "tools": tools, "events": events,
            "started": first_ts, "ended": last_ts}


class SessionIngestor:
    """Ingest Claude Code session transcripts into the memory graph.

    Reads JSONL transcripts under a projects directory, redacts secrets, and
    writes one episodic ``session`` memory node per new transcript. Also records
    ``signal='used'`` retrieval-feedback rows when a session edited a file that
    RAG recently returned. Dedupes via ``session_ingest_state``.
    """

    def __init__(
        self,
        db: IDatabase,
        mem_repo: IMemoryRepository,
        secret_detector: SecretDetector | None = None,
        embedder: IEmbeddingProvider | None = None,
    ) -> None:
        self.db = db
        self.mem_repo = mem_repo
        self.redact = (secret_detector or SecretDetector()).redact
        self.embedder = embedder

    @classmethod
    def default(cls) -> "SessionIngestor":
        """Build a SessionIngestor wired from the default config/providers."""
        from claudenv.adapters.config import get_database_config

        db = SQLiteDatabase(get_database_config().get_database_dsn())
        mem_repo = SQLiteMemoryRepository(db)
        embedder: IEmbeddingProvider | None = None
        try:
            from claudenv.adapters.embedding import get_embedder
            embedder = get_embedder()
        except Exception:
            logger.warning("embedder unavailable; proceeding without embeddings",
                           exc_info=True)
        return cls(db, mem_repo, SecretDetector(), embedder)

    # -- internals ---------------------------------------------------------
    def _record_usage_signals(self, repo: str, edited: list[str],
                              session_id: str) -> int:
        """If an edited file was recently retrieved by RAG, record signal='used'."""
        n = 0
        for fp in edited:
            # match on path suffix: transcripts hold absolute paths, the index
            # holds repo-relative ones
            rows = self.db.query(
                "SELECT DISTINCT chunk_id, file_path, branch FROM rag_chunk_feedback "
                "WHERE repo=? AND signal='retrieved' "
                f"AND ts >= datetime('now', '-{USED_WINDOW_HOURS} hours') "
                "AND ? LIKE '%' || file_path",
                (repo, fp),
            )
            for r in rows:
                self.db.execute(
                    "INSERT INTO rag_chunk_feedback "
                    "(ts,repo,branch,chunk_id,file_path,query_hash,signal,session_id) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (_now(), repo, r["branch"], r["chunk_id"], r["file_path"],
                     None, "used", session_id),
                )
                n += 1
        return n

    # -- public ------------------------------------------------------------
    def ingest(self, transcripts_dir: Path, dry_run: bool = False) -> dict:
        stats = {"scanned": 0, "ingested": 0, "skipped": 0, "not_onboarded": 0,
                 "usage_signals": 0}

        for tpath in sorted(transcripts_dir.glob("*/*.jsonl")):
            sid = tpath.stem
            stats["scanned"] += 1
            if self.db.query_one(
                "SELECT 1 FROM session_ingest_state WHERE session_id=?", (sid,)
            ):
                continue

            summary = _parse_transcript(tpath)
            cwd = summary["cwd"] if summary else None

            # Onboarding gate: this ingestor bypasses the per-repo native-tool
            # hooks entirely (it reads ~/.claude/projects/ directly), so a
            # session from a repo that was never `claude-env register`-ed must
            # never reach the memory graph. No basename fallback -- absence of a
            # repo-policy.yaml means skip, not "guess a label and ingest anyway".
            repo = _repo_slug(cwd, fallback_to_basename=False) if cwd else None
            if summary is not None and cwd and not repo:
                stats["not_onboarded"] += 1
                stats["skipped"] += 1
                if dry_run:
                    print(f"would skip {sid[:8]} cwd={cwd} (not onboarded)")
                if not dry_run:
                    self.db.execute(
                        "INSERT OR IGNORE INTO session_ingest_state "
                        "(session_id,transcript_path,repo,node_id,events,ingested_at) "
                        "VALUES (?,?,?,?,?,?)",
                        (sid, str(tpath), None, None, summary["events"], _now()),
                    )
                continue

            if summary is None or not repo:
                stats["skipped"] += 1
                if not dry_run:
                    self.db.execute(
                        "INSERT OR IGNORE INTO session_ingest_state "
                        "(session_id,transcript_path,repo,node_id,events,ingested_at) "
                        "VALUES (?,?,?,?,?,?)",
                        (sid, str(tpath), repo, None,
                         summary["events"] if summary else 0, _now()),
                    )
                continue

            if dry_run:
                print(f"would ingest {sid[:8]} repo={repo} "
                      f"files={len(summary['files'])} "
                      f"tools={sum(summary['tools'].values())}")
                stats["ingested"] += 1
                continue

            writer = MemoryWriter(
                repo=self.mem_repo,
                namespace=f"proj-{repo}",
                embedding=self.embedder,
            )
            body = {
                "task": self.redact(summary["task"]),
                "outcome": self.redact(summary["outcome"]),
                "files_touched": summary["files"],
                "files_edited": summary["edited"],
                "tools_used": summary["tools"],
                "events": summary["events"],
                "started": summary["started"], "ended": summary["ended"],
                "transcript": str(tpath),
            }
            name = (f"session {sid[:8]}: "
                    f"{self.redact(summary['task'])[:80] or 'untitled'}")
            node_id = writer.add_node(
                MemoryType.EPISODIC, "session", name, body,
                repo=repo, confidence=0.9,
            )
            used = self._record_usage_signals(repo, summary["edited"], sid)
            self.db.execute(
                "INSERT INTO session_ingest_state "
                "(session_id,transcript_path,repo,node_id,events,ingested_at) "
                "VALUES (?,?,?,?,?,?)",
                (sid, str(tpath), repo, str(node_id), summary["events"], _now()),
            )
            stats["ingested"] += 1
            stats["usage_signals"] += used
        return stats


def ingest(transcripts_dir: Path, dry_run: bool = False) -> dict:
    """Ingest all new transcripts under ``transcripts_dir`` (default wiring)."""
    return SessionIngestor.default().ingest(transcripts_dir, dry_run=dry_run)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Ingest Claude Code session transcripts into memory"
    )
    ap.add_argument("--transcripts", default=str(DEFAULT_TRANSCRIPTS))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    tdir = Path(args.transcripts).expanduser()
    if not tdir.is_dir():
        print(f"no transcripts directory at {tdir} -- nothing to ingest")
        return 0
    stats = SessionIngestor.default().ingest(tdir, dry_run=args.dry_run)
    print(f"transcripts scanned={stats['scanned']} ingested={stats['ingested']} "
          f"skipped={stats['skipped']} (not_onboarded={stats['not_onboarded']}) "
          f"usage_signals={stats['usage_signals']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
