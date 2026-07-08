#!/usr/bin/env python3
"""
claude-env :: Claude Code session-transcript ingestor
File: memory/session_ingestor.py
Purpose:
    Make the memory graph SELF-POPULATING. Claude Code stores every session as
    a JSONL transcript under ~/.claude/projects/<munged-cwd>/<session>.jsonl.
    This ingestor parses new transcripts and writes one episodic `session`
    memory node per session: the task, files touched, tools used, and outcome.
    The next session in that repo can recall what previous sessions did, with
    zero user discipline required.

    It also closes the retrieval feedback loop: if a file that the RAG pipeline
    previously returned (rag_chunk_feedback signal='retrieved') was edited in a
    session, a signal='used' row is recorded — a heuristic time-window
    correlation that the retriever turns into a ranking boost.

Privacy: node bodies pass through SecretDetector.redact() before storage; the
transcript itself never leaves disk. Dedupe via session_ingest_state.

Usage:
    python memory/session_ingestor.py                  # ingest all new transcripts
    python memory/session_ingestor.py --dry-run        # report only
    python memory/session_ingestor.py --transcripts /custom/dir
    (scheduled nightly via scripts/nightly_memory.sh)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.db import get_db                          # noqa: E402
from memory.memory_manager import MemoryManager    # noqa: E402
from security.detectors import SecretDetector      # noqa: E402
from lib.logging_setup import get_logger           # noqa: E402

_log = get_logger("memory")

DEFAULT_TRANSCRIPTS = Path.home() / ".claude" / "projects"
MIN_EVENTS = 3          # skip trivial sessions
MAX_OUTCOME = 400       # chars of final assistant text stored
USED_WINDOW_HOURS = 24  # retrieval->edit correlation window


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _text_of(content) -> str:
    """Flatten a Claude message content field (str or block list) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text")
    return ""


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
                # whole transcript; skip it (debug — transcripts can be large).
                _log.debug("skipping unparseable transcript line in %s",
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
                            fp = tin.get("file_path") or tin.get("path") \
                                or tin.get("notebook_path")
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


def _record_usage_signals(db, repo: str, edited: list[str], session_id: str) -> int:
    """If an edited file was recently retrieved by RAG, record signal='used'."""
    n = 0
    for fp in edited:
        # match on path suffix: transcripts hold absolute paths, the index holds
        # repo-relative ones
        rows = db.query(
            "SELECT DISTINCT chunk_id, file_path, branch FROM rag_chunk_feedback "
            "WHERE repo=? AND signal='retrieved' "
            f"AND ts >= datetime('now', '-{USED_WINDOW_HOURS} hours') "
            "AND ? LIKE '%' || file_path", (repo, fp))
        for r in rows:
            db.execute(
                "INSERT INTO rag_chunk_feedback "
                "(ts,repo,branch,chunk_id,file_path,query_hash,signal,session_id) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (_now(), repo, r["branch"], r["chunk_id"], r["file_path"],
                 None, "used", session_id))
            n += 1
    return n


def ingest(transcripts_dir: Path, dry_run: bool = False) -> dict:
    db = get_db()
    redact = SecretDetector(session_id="ingest").redact
    stats = {"scanned": 0, "ingested": 0, "skipped": 0, "usage_signals": 0}

    for tpath in sorted(transcripts_dir.glob("*/*.jsonl")):
        sid = tpath.stem
        stats["scanned"] += 1
        if db.query_one("SELECT 1 FROM session_ingest_state WHERE session_id=?", (sid,)):
            continue

        summary = _parse_transcript(tpath)
        repo = Path(summary["cwd"]).name if summary and summary["cwd"] else None
        if summary is None or not repo:
            stats["skipped"] += 1
            if not dry_run:
                db.execute(
                    "INSERT OR IGNORE INTO session_ingest_state "
                    "(session_id,transcript_path,repo,node_id,events,ingested_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (sid, str(tpath), repo, None,
                     summary["events"] if summary else 0, _now()))
            continue

        if dry_run:
            print(f"would ingest {sid[:8]} repo={repo} "
                  f"files={len(summary['files'])} tools={sum(summary['tools'].values())}")
            stats["ingested"] += 1
            continue

        mgr = MemoryManager(namespace=f"proj-{repo}", session_id="ingest",
                            actor="session-ingestor")
        body = {
            "task": redact(summary["task"]),
            "outcome": redact(summary["outcome"]),
            "files_touched": summary["files"],
            "files_edited": summary["edited"],
            "tools_used": summary["tools"],
            "events": summary["events"],
            "started": summary["started"], "ended": summary["ended"],
            "transcript": str(tpath),
        }
        name = f"session {sid[:8]}: {redact(summary['task'])[:80] or 'untitled'}"
        node_id = mgr.add_node("episodic", "session", name, body, repo=repo,
                               confidence=0.9)
        used = _record_usage_signals(db, repo, summary["edited"], sid)
        db.execute(
            "INSERT INTO session_ingest_state "
            "(session_id,transcript_path,repo,node_id,events,ingested_at) "
            "VALUES (?,?,?,?,?,?)",
            (sid, str(tpath), repo, node_id, summary["events"], _now()))
        stats["ingested"] += 1
        stats["usage_signals"] += used
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest Claude Code session transcripts into memory")
    ap.add_argument("--transcripts", default=str(DEFAULT_TRANSCRIPTS))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    tdir = Path(args.transcripts).expanduser()
    if not tdir.is_dir():
        print(f"no transcripts directory at {tdir} — nothing to ingest")
        return 0
    stats = ingest(tdir, dry_run=args.dry_run)
    print(f"transcripts scanned={stats['scanned']} ingested={stats['ingested']} "
          f"skipped={stats['skipped']} usage_signals={stats['usage_signals']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
