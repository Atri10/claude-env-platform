"""
claude-env :: Application - RAG - Unified recall across memory, code index, and git

KnowPipeline fans one query out across every local knowledge source and fuses
the results with provenance:

  * memory graph   -- decisions, conventions, session learnings
                      (proj-<repo> + global namespaces)
  * RAG index      -- hybrid + reranked code/doc chunks via RagService
  * git history    -- `git log --grep` + `git grep` line hits

Each source degrades gracefully: no RAG service -> RAG section skipped with a
note; not a git repo -> git section skipped; memory always works when a
memory-graph factory is wired. RAG chunks are poison-screened
(RagPoisonDetector) before delivery and wrapped in <retrieved_context> data
delimiters so retrieved text can never act as an instruction to the agent.

Ported from the pre-refactor `rag/pipelines/know.py`, which the hexagonal
rewrite ("Refactor Genesis") dropped. The flat-layout free functions became a
class with injected collaborators (RagService, an IMemoryGraph factory, and a
RagPoisonDetector) so the pipeline is testable and free of hidden global state.
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
from collections.abc import Callable
from pathlib import Path

from claudenv.application.rag.service import RagService
from claudenv.domain.memory.service.graph import MemoryGraph
from claudenv.domain.security import RagPoisonDetector
from claudenv.domain.value_objects import BranchName, RepoSlug
from claudenv.ports import IMemoryGraph

logger = logging.getLogger(__name__)
# Injection-safe delimiters wrapping every retrieved code/doc chunk, mirroring
# rag/pipelines/retrieve.py's CHUNK_OPEN/CHUNK_CLOSE contract: retrieved text is
# data, never instructions.
CHUNK_OPEN = '<retrieved_context source="rag" path="{path}" lines="{a}-{b}">'
CHUNK_CLOSE = "</retrieved_context>"

# Body keys worth surfacing from a memory node (matches the original pipeline).
_MEMORY_BODY_KEYS = (
    "task", "why", "desc", "outcome", "decision", "files_edited", "summary",
)


def _node_to_dict(node) -> dict:
    """Flatten a MemoryNode into the dict shape the original pipeline emitted."""
    return {
        "name": node.name,
        "kind": node.node_kind.value if hasattr(node.node_kind, "value") else str(node.node_kind),
        "namespace": node.namespace,
        "confidence": round(node.effective_confidence, 4),
        "updated": node.updated_at.isoformat()[:10],
        "body": {k: v for k, v in (node.body or {}).items() if k in _MEMORY_BODY_KEYS},
    }


class KnowPipeline:
    """Unified recall ("what do I know about X") with provenance.

    Three sources are fused; each degrades independently so the command is
    useful from the first day, before any model download:

      * memory graph   -- MemoryGraph.recall over proj-<repo> + global
      * RAG index      -- RagService.search (hybrid + rerank), poison-screened
      * git history    -- `git log --grep` + `git grep`
    """

    def __init__(
        self,
        rag_service: RagService | None = None,
        memory_graph_factory: Callable[[str], IMemoryGraph] | None = None,
        poison_detector: RagPoisonDetector | None = None,
    ) -> None:
        # Factory builds a namespace-scoped IMemoryGraph (proj-<repo>, global).
        # When absent (e.g. DB not bootstrapped) the memory section degrades to
        # empty rather than crashing. Mirrors ContextPackGenerator's injection.
        self._rag = rag_service
        self._memory_graph_factory = memory_graph_factory
        self._poison = poison_detector or RagPoisonDetector()

    # --- memory -----------------------------------------------------------

    def memory_section(self, query: str, repo: str, top_k: int = 8) -> list[dict]:
        if self._memory_graph_factory is None:
            return []
        # The original MemoryRetriever searched proj-<repo> + global namespaces
        # together (extra_ns=["global"]). The hexagonal MemoryGraphTraversal
        # only keyword-searches its own namespace, so we recall from each
        # namespace in turn and merge by node_id to preserve that coverage.
        namespaces = [f"proj-{repo}", "global"]
        try:
            seen: dict[str, object] = {}
            for ns in namespaces:
                mgr = self._memory_graph_factory(ns)
                for n in mgr.recall(
                    query, depth=2, top_k=top_k, extra_namespaces=["global"],
                ):
                    seen.setdefault(str(n.node_id), n)
            hits = list(seen.values())
            if not hits and " " in query:
                # phrase missed: retry per-term and merge (keyword recall is
                # LIKE-based substring match, so a multi-word query can miss).
                for term in query.split():
                    if len(term) < 3:
                        continue
                    for ns in namespaces:
                        mgr = self._memory_graph_factory(ns)
                        for n in mgr.recall(
                            term, depth=1, top_k=top_k, extra_namespaces=["global"],
                        ):
                            seen.setdefault(str(n.node_id), n)
                hits = list(seen.values())
            return [_node_to_dict(n) for n in hits[:top_k]]
        except Exception:
            # DB not bootstrapped or repo missing -- memory fills as sessions
            # are ingested, so degrade to empty rather than failing the query.
            logger.info(
                "memory section unavailable for repo %s; skipping",
                repo, exc_info=True,
            )
            return []

    # --- RAG --------------------------------------------------------------

    def rag_section(
        self, query: str, repo: str, branch: str, top_n: int = 6,
    ) -> tuple[list[dict], str]:
        """Returns (hits, note). Empty hits + note when RAG is unavailable."""
        if self._rag is None:
            return [], "RAG unavailable (no rag service configured)"
        try:
            results = self._rag.search(
                repo=RepoSlug.from_string(repo),
                branch=BranchName.from_string(branch),
                query=query,
                top_k=top_n,
            )
        except Exception as exc:
            return [], f"RAG unavailable ({str(exc)[:90]})"

        hits: list[dict] = []
        denied = 0
        for r in results:
            chunk = r.chunk
            source = f"{repo}@{branch}:{chunk.file_path}"
            # Poison-screen each chunk before delivery: blocked chunks are
            # dropped entirely; sub-threshold hits are flagged as suspect.
            verdict = self._poison.scan_chunk(chunk.text, source)
            if verdict.blocked:
                denied += 1
                logger.warning(
                    "denied poisoned chunk from %s: %s", source, verdict.reasons,
                )
                continue
            hits.append({
                "file": chunk.file_path,
                "lines": f"{chunk.start_line}-{chunk.end_line}",
                "score": round(r.score, 3),
                "preview": chunk.text[:160],
                "flagged": verdict.flagged,
            })
        note = f"RAG: {denied} chunk(s) denied by poison screen" if denied else ""
        return hits, note

    # --- git --------------------------------------------------------------

    def git_section(self, query: str, repo_root: Path, limit: int = 8) -> dict:
        if not (repo_root / ".git").exists():
            return {"note": f"{repo_root} is not a git repository"}
        out: dict = {}
        log = subprocess.run(
            ["git", "-C", str(repo_root), "log", "--grep", query, "-i",
             f"--max-count={limit}", "--format=%h %ad %s", "--date=short"],
            capture_output=True, text=True,
        )
        out["commits"] = log.stdout.strip().splitlines() if log.returncode == 0 else []
        grep = subprocess.run(
            ["git", "-C", str(repo_root), "grep", "-in", "--max-count=2", query],
            capture_output=True, text=True,
        )
        out["code_lines"] = (
            grep.stdout.strip().splitlines()[:limit] if grep.returncode == 0 else []
        )
        return out

    # --- fusion -----------------------------------------------------------

    def know(
        self, query: str, repo: str, branch: str, repo_root: Path | None,
    ) -> dict:
        result: dict = {
            "query": query,
            "repo": repo,
            "memory": self.memory_section(query, repo),
        }
        rag_hits, rag_note = self.rag_section(query, repo, branch)
        result["rag"] = rag_hits
        if rag_note:
            result["rag_note"] = rag_note
        if repo_root:
            result["git"] = self.git_section(query, repo_root)
        return result

    def format_text(self, r: dict) -> str:
        """Human-readable, delimiter-wrapped rendering of a know() result."""
        lines = [
            f"# what claude-env knows about: {r['query']!r}  (repo: {r['repo']})\n",
            f"## memory graph ({len(r['memory'])})",
        ]
        for m in r["memory"]:
            lines.append(
                f"  [{m['kind']:12s}] ({m['confidence']:.2f}, {m['updated']}) {m['name']}"
            )
            for k, v in (m["body"] or {}).items():
                lines.append(f"      {k}: {str(v)[:120]}")
        if not r["memory"]:
            lines.append("  (nothing yet -- memory fills as sessions are ingested)")

        lines.append(
            f"\n## code index ({len(r['rag'])})"
            + (f"  -- {r['rag_note']}" if r.get("rag_note") else "")
        )
        for h in r["rag"]:
            tag = " [FLAGGED]" if h.get("flagged") else ""
            lines.append(
                f"  [{h['score']:>6.3f}]{tag} {h['file']}:{h['lines']}"
            )
            # wrap the untrusted preview in data delimiters
            lines.append("  " + CHUNK_OPEN.format(
                path=h["file"], a=h["lines"].split("-")[0], b=h["lines"].split("-")[-1]))
            lines.append(f"  {h['preview'][:90]!r}")
            lines.append("  " + CHUNK_CLOSE)

        if "git" in r:
            g = r["git"]
            if g.get("note"):
                lines.append(f"\n## git -- {g['note']}")
            else:
                lines.append(
                    f"\n## git commits mentioning it ({len(g.get('commits', []))})"
                )
                for c in g.get("commits", []):
                    lines.append(f"  {c}")
                lines.append(f"## git code lines ({len(g.get('code_lines', []))})")
                for line in g.get("code_lines", []):
                    lines.append(f"  {line[:140]}")
        return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Unified local recall with provenance")
    ap.add_argument("query")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--branch", default="main")
    ap.add_argument("--repo-root", help="path to the working tree for git search")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    args = ap.parse_args()

    root = Path(args.repo_root).resolve() if args.repo_root else None
    rag: RagService | None = None
    mem_factory: Callable[[str], IMemoryGraph] | None = None
    try:
        from claudenv.di import get_container
        from claudenv.ports import IEmbeddingProvider, IMemoryRepository

        c = get_container()
        rag = c.get(RagService)
        mem_repo = c.get(IMemoryRepository)
        try:
            embedder = c.get(IEmbeddingProvider)
        except Exception:
            embedder = None

        def _mem_factory(ns: str) -> IMemoryGraph:
            return MemoryGraph(mem_repo, ns, embedding=embedder, isolated=False)

        mem_factory = _mem_factory
    except Exception:
        # DI/DB unavailable -- still produce a result from whatever sources
        # remain (git + any memory already covered by a wired factory).
        logger.info("DI unavailable; running know with degraded sources", exc_info=True)

    pipe = KnowPipeline(rag_service=rag, memory_graph_factory=mem_factory)
    result = pipe.know(args.query, args.repo, args.branch, root)
    if args.format == "json":
        print(json.dumps(result, indent=2, default=str))
    else:
        print(pipe.format_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
