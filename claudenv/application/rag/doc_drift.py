"""
claude-env :: Application - RAG Service - Documentation Drift Detector

Purpose:
    Find documentation that has drifted from the code it describes:
      * broken references — a doc names a file path that no longer exists
      * stale docs        — the referenced code changed in git AFTER the doc
                            was last touched (the doc may describe old behavior)
      * (semantic=True)   — optionally embed the doc paragraph and the code
                            head and flag low similarity (needs the configured
                            embedding model; skipped gracefully otherwise)

    Runs fully offline against the working tree + git history. Used standalone
    or as part of the nightly analyst digest.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# path-looking tokens with a code/config extension, optionally backticked.
# (?<![$\w{/.-]) — not a $VAR/${VAR}, not the tail of an absolute or hyphenated
#                  path (mcp-servers -> 'servers/..'), not after a dot-dir (.claude/)
# (?![A-Za-z0-9_]) — the extension must actually end there (no 'llama.c' in 'llama.cpp')
_REF = re.compile(
    r"(?<![$\w{/.-])([A-Za-z0-9_][A-Za-z0-9_./-]*\."
    r"(?:py|tsx?|jsx?|go|java|rb|rs|cc|cpp|c|h|sql|sh|zsh|ya?ml|json|toml))"
    r"(?![A-Za-z0-9_])")
_SKIP_PREFIX = ("http", "<", "{")
# well-known project names that look like file refs but aren't
_KNOWN_NONFILES = {"llama.cpp", "tree-sitter.c"}
MAX_PAIRS = 300


def _git_commit_ts(root: Path, path: str) -> int:
    r = subprocess.run(["git", "-C", str(root), "log", "-1", "--format=%ct",
                       "--", path], capture_output=True, text=True)
    try:
        return int(r.stdout.strip())
    except Exception:
        return 0


def _md_files(root: Path) -> list[str]:
    r = subprocess.run(["git", "-C", str(root), "ls-files", "*.md", "**/*.md"],
                       capture_output=True, text=True)
    files = [f for f in r.stdout.splitlines() if f] if r.returncode == 0 else []
    return sorted(set(files))


class DocDriftDetector:
    """Detect documentation that has drifted from the code it describes.

    Operates on a working tree plus its git history. ``semantic=True`` adds an
    optional embedding-based similarity pass; it is skipped gracefully when no
    embedding model is configured.
    """

    def __init__(self, root: Path, semantic: bool = False) -> None:
        self.root = Path(root).resolve()
        self.semantic = semantic

    def scan(self) -> dict:
        """Run the drift scan and return a structured report."""
        broken: list[dict] = []
        stale: list[dict] = []
        low_sim: list[dict] = []
        pairs = 0
        embedder = None
        if self.semantic:
            try:
                from claudenv.adapters.embedding import get_embedder
                embedder = get_embedder()
            except Exception as exc:
                self.semantic = False
                sem_note = f"semantic check skipped: {str(exc)[:80]}"
        sem_note = "" if self.semantic or not embedder else ""

        md_files = _md_files(self.root)
        for md in md_files:
            try:
                text = (self.root / md).read_text(errors="ignore")
            except Exception:
                continue
            refs = {m.group(1) for m in _REF.finditer(text)
                    if not m.group(1).startswith(_SKIP_PREFIX)
                    and m.group(1) not in _KNOWN_NONFILES}
            doc_ts = _git_commit_ts(self.root, md)
            for ref in sorted(refs):
                if pairs >= MAX_PAIRS:
                    break
                pairs += 1
                # resolve relative to repo root, then to the doc's directory
                cand: Path | None = self.root / ref
                if not cand.exists():
                    cand = (self.root / md).parent / ref
                if not cand.exists():
                    # path fragments like 'rag/config.py' referenced from anywhere
                    matches = list(self.root.glob(f"**/{ref}"))
                    cand = matches[0] if matches else None
                if cand is None or not cand.exists():
                    broken.append({"doc": md, "ref": ref})
                    continue
                rel = str(cand.relative_to(self.root))
                code_ts = _git_commit_ts(self.root, rel)
                if doc_ts and code_ts and code_ts > doc_ts:
                    stale.append({"doc": md, "ref": rel,
                                  "code_newer_by_days": round((code_ts - doc_ts) / 86400, 1)})
                if self.semantic and embedder is not None:
                    try:
                        para = next((p for p in text.split("\n\n") if ref in p), "")[:1000]
                        head = cand.read_text(errors="ignore")[:2000]
                        dv = embedder.embed_documents([para])[0]
                        cv = embedder.embed_documents([head])[0]
                        sim = sum(a * b for a, b in zip(dv, cv))
                        if sim < 0.25:
                            low_sim.append({"doc": md, "ref": rel, "similarity": round(sim, 3)})
                    except Exception:
                        # per-reference similarity check is best-effort; skip
                        # this ref on failure rather than abort the whole scan.
                        logger.debug("semantic drift check failed for ref %s in %s",
                                     rel, md, exc_info=True)

        return {"repo": str(self.root), "docs_scanned": len(md_files),
                "reference_pairs": pairs,
                "broken_references": broken, "stale_docs": stale,
                "low_similarity": low_sim,
                **({"note": sem_note} if sem_note else {})}


def scan(root: Path, semantic: bool = False) -> dict:
    """Module-level convenience wrapper around :class:`DocDriftDetector`."""
    return DocDriftDetector(root, semantic=semantic).scan()


def main() -> int:
    ap = argparse.ArgumentParser(description="Detect doc/code drift")
    ap.add_argument("repo_root")
    ap.add_argument("--semantic", action="store_true",
                    help="also embed doc/code pairs (needs configured model)")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    args = ap.parse_args()

    r = DocDriftDetector(Path(args.repo_root), semantic=args.semantic).scan()
    if args.format == "json":
        print(json.dumps(r, indent=2))
        return 0
    print(f"# doc drift — {r['repo']}  "
          f"(docs: {r['docs_scanned']}, refs checked: {r['reference_pairs']})")
    print(f"\nbroken references: {len(r['broken_references'])}")
    for b in r["broken_references"][:30]:
        print(f"  {b['doc']} -> {b['ref']} (missing)")
    print(f"\nstale docs (code changed after doc): {len(r['stale_docs'])}")
    for s in r["stale_docs"][:30]:
        print(f"  {s['doc']} -> {s['ref']} (code newer by {s['code_newer_by_days']}d)")
    if r["low_similarity"]:
        print(f"\nlow doc/code similarity: {len(r['low_similarity'])}")
        for s in r["low_similarity"][:15]:
            print(f"  {s['doc']} -> {s['ref']} (sim {s['similarity']})")
    return 1 if r["broken_references"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
