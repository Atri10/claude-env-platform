"""
claude-env :: Application - Memory - Nightly consolidation job

NightlyAnalyst: background memory-maintenance use case. Scheduled nightly
(scripts/nightly_memory.sh / launchd / systemd), it:

    1. ingests new Claude Code session transcripts into the memory graph
       (SessionIngestor -> one episodic ``session`` node per transcript),
    2. consolidates low-confidence clusters into summary nodes
       (MemoryConsolidator, merging clusters of >=5 nodes with >=3 low-
       confidence members),
    3. prunes stale entries -- archiving, not deleting (MemoryPruner with
       archive=True, which stamps ``superseded_by='archived:<date>'`` so the
       rows stay in the graph for audit but are excluded from active reads),
    4. writes a run summary: a digest file under
       ``$CLAUDE_ENV_HOME/logs/digests/nightly-<date>.md`` and an
       episodic/investigation memory node in the ``nightly`` namespace so the
       next session starts already knowing the last maintenance pass.

Operates across every populated memory namespace discovered in the DB so a
single scheduled run maintains all onboarded repos.

Usage:
    python -m claudenv.application.memory.nightly
    python -m claudenv.application.memory.nightly --dry-run
    python -m claudenv.application.memory.nightly --transcripts /custom/dir
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from claudenv.adapters.config import get_config
from claudenv.adapters.persistence import SQLiteDatabase, SQLiteMemoryRepository
from claudenv.application.memory.session_ingestor import SessionIngestor
from claudenv.domain.memory import MemoryType
from claudenv.domain.memory.service.maintenance import (
    MemoryConsolidator,
    MemoryPruner,
)
from claudenv.domain.memory.service.writer import MemoryWriter
from claudenv.domain.security import SecretDetector
from claudenv.ports import IDatabase, IMemoryRepository

logger = logging.getLogger(__name__)

DEFAULT_TRANSCRIPTS = Path.home() / ".claude" / "projects"


class NightlyAnalyst:
    """Nightly memory consolidation job.

    Orchestrates ingestion -> consolidation -> archival pruning -> summary for
    every populated memory namespace in a single scheduled run.
    """

    def __init__(
        self,
        db: IDatabase,
        mem_repo: IMemoryRepository,
        namespace: str = "nightly",
        config: Any | None = None,
        session_ingestor: SessionIngestor | None = None,
        redactor: SecretDetector | None = None,
    ) -> None:
        self.db = db
        self.mem_repo = mem_repo
        self.namespace = namespace
        self.config = config or get_config()
        self.ingestor = session_ingestor or SessionIngestor(db, mem_repo)
        self.redact = (redactor or SecretDetector()).redact

    @classmethod
    def default(cls, namespace: str = "nightly") -> NightlyAnalyst:
        """Wire a NightlyAnalyst from the default config/providers."""
        from claudenv.adapters.config import get_database_config

        db = SQLiteDatabase(get_database_config().get_database_dsn())
        mem_repo = SQLiteMemoryRepository(db)
        return cls(db, mem_repo, namespace=namespace)

    # -- internals ---------------------------------------------------------
    def _namespaces(self) -> list[str]:
        """Distinct non-nightly namespaces that currently hold memory nodes.

        Consolidation and pruning run per-namespace; the ``nightly`` summary
        namespace is excluded (it holds the run summaries, not project memory).
        """
        try:
            rows = self.db.query(
                "SELECT DISTINCT namespace FROM memory_nodes "
                "WHERE namespace IS NOT NULL ORDER BY namespace"
            )
            return [r["namespace"] for r in rows
                    if r["namespace"] and r["namespace"] != self.namespace]
        except Exception:
            logger.warning(
                "namespace discovery failed; no maintenance targets",
                exc_info=True,
            )
            return []

    def _write_digest(self, summary: dict[str, Any]) -> Path | None:
        """Write the run summary to ``$CLAUDE_ENV_HOME/logs/digests``.

        Returns the digest path, or None when CLAUDE_ENV_HOME is unresolvable
        (the run still completes; the digest file is a convenience surface).
        """
        try:
            home = Path(self.config.get_claude_env_home())
        except Exception:
            logger.warning(
                "could not resolve CLAUDE_ENV_HOME; skipping digest file",
                exc_info=True,
            )
            return None
        out_dir = home / "logs" / "digests"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"nightly-{summary['date']}.md"
        out.write_text(self._render(summary))
        return out

    @staticmethod
    def _render(s: dict[str, Any]) -> str:
        ing = s["ingest"]
        con = s["consolidation"]
        pru = s["pruning"]
        namespaces = s["namespaces"]
        lines = [
            f"# nightly memory consolidation -- {s['date']}", "",
            "## session ingestion",
            f"- scanned: {ing['scanned']}",
            f"- ingested: {ing['ingested']}",
            f"- skipped: {ing['skipped']} "
            f"(not onboarded: {ing['not_onboarded']})",
            f"- usage signals: {ing['usage_signals']}", "",
            f"## consolidation ({len(namespaces)} namespace(s))",
            f"- consolidated clusters: {con['consolidated_clusters']}",
            f"- dry_run: {con['dry_run']}",
        ]
        lines += [f"  - `{ns}`" for ns in namespaces]
        lines += [
            "", "## pruning (archive, not delete)",
            f"- candidates: {pru['candidates']}",
            f"- archived: {pru['archived']}",
            f"- pruned (hard): {pru['pruned']}",
            f"- dry_run: {pru['dry_run']}", "",
        ]
        return "\n".join(lines)

    def _write_summary_node(
        self, summary: dict[str, Any], dry_run: bool,
    ) -> str | None:
        """Write an episodic/investigation node summarising this run.

        The node lands in the ``nightly`` namespace so the next session can
        recall the most recent maintenance pass. Returns the node id, or None
        when dry_run skips the write.
        """
        if dry_run:
            return None
        writer = MemoryWriter(repo=self.mem_repo, namespace=self.namespace)
        name = f"nightly consolidation {summary['date']}"
        body = {
            "ingest": summary["ingest"],
            "consolidation": summary["consolidation"],
            "pruning": summary["pruning"],
            "namespaces": summary["namespaces"],
            "digest_path": (str(summary["digest_path"])
                            if summary["digest_path"] else None),
        }
        node_id = writer.add_node(
            MemoryType.EPISODIC, "investigation", name, body,
            repo=None, confidence=0.8,
        )
        return str(node_id)

    # -- public ------------------------------------------------------------
    def run(
        self,
        dry_run: bool = False,
        transcripts_dir: Path | None = None,
    ) -> dict[str, Any]:
        """Run one nightly maintenance pass and return the summary dict.

        Steps: ingest transcripts -> consolidate per namespace -> prune
        (archive) per namespace -> write summary (digest file + memory node).
        """
        date = datetime.now(UTC).strftime("%Y-%m-%d")

        # 1. ingest new session transcripts
        tdir = Path(transcripts_dir) if transcripts_dir else DEFAULT_TRANSCRIPTS
        if tdir.is_dir():
            ingest_stats = self.ingestor.ingest(tdir, dry_run=dry_run)
        else:
            logger.info("no transcripts directory at %s; skipping ingestion", tdir)
            ingest_stats = {
                "scanned": 0, "ingested": 0, "skipped": 0,
                "not_onboarded": 0, "usage_signals": 0,
            }

        # 2. consolidate low-confidence clusters per namespace
        namespaces = self._namespaces()
        consolidated_total = 0
        for ns in namespaces:
            consolidator = MemoryConsolidator(self.mem_repo, ns)
            cstats = consolidator.run(dry_run=dry_run)
            consolidated_total += cstats.get("consolidated_clusters", 0)
        consolidation_stats = {
            "consolidated_clusters": consolidated_total,
            "dry_run": dry_run,
        }

        # 3. prune stale entries -- archive, not delete -- per namespace
        candidates_total = 0
        archived_total = 0
        pruned_total = 0
        for ns in namespaces:
            pruner = MemoryPruner(self.mem_repo, ns)
            pstats = pruner.run(dry_run=dry_run, archive=True)
            candidates_total += pstats.get("candidates", 0)
            archived_total += pstats.get("archived", 0)
            pruned_total += pstats.get("pruned", 0)
        pruning_stats = {
            "candidates": candidates_total,
            "archived": archived_total,
            "pruned": pruned_total,
            "dry_run": dry_run,
        }

        # 4. write the summary (digest file + memory node)
        summary: dict[str, Any] = {
            "date": date,
            "ingest": ingest_stats,
            "consolidation": consolidation_stats,
            "pruning": pruning_stats,
            "namespaces": namespaces,
            "digest_path": None,
            "summary_node_id": None,
        }
        summary["digest_path"] = self._write_digest(summary)
        summary["summary_node_id"] = self._write_summary_node(summary, dry_run)
        return summary


def main() -> int:
    """CLI entry point for the nightly consolidation job."""
    import argparse

    ap = argparse.ArgumentParser(description="Nightly memory consolidation job")
    ap.add_argument("--transcripts", default=str(DEFAULT_TRANSCRIPTS),
                    help="transcripts directory (default ~/.claude/projects)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report only; write nothing")
    ap.add_argument("--namespace", default="nightly",
                    help="namespace for the summary memory node")
    args = ap.parse_args()

    analyst = NightlyAnalyst.default(namespace=args.namespace)
    summary = analyst.run(
        dry_run=args.dry_run,
        transcripts_dir=Path(args.transcripts),
    )
    print(
        f"nightly consolidation {summary['date']}: "
        f"ingested={summary['ingest']['ingested']} "
        f"consolidated={summary['consolidation']['consolidated_clusters']} "
        f"archived={summary['pruning']['archived']}"
    )
    if summary["digest_path"]:
        print(f"digest -> {summary['digest_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
