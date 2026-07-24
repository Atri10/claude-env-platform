"""
claude-env :: Application - Documentation Service
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from claudenv.domain.security import RagPoisonDetector
from claudenv.domain.value_objects import Tier

logger = logging.getLogger(__name__)

_SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", ".claude-env", "dist", "build"}
_README_NAMES = ("README.md", "README.rst", "README.txt", "readme.md")
_SYMBOL_EXTS = (".py", ".js", ".ts")
_DEF_PATTERNS = {
    "class": re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)"),
    "function": re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)"),
}


class DocsService:
    """Documentation lookup service.

    Two families of behaviour:
      - Repo introspection (list_dependencies/get_schema/find_reference/
        get_file/get_readme): simple, best-effort local filesystem lookups.
      - search_local/fetch_external: ported from the pre-refactor documentation
        MCP server -- local docs-corpus keyword search, and the platform's
        *sole* sanctioned outbound network call, tier-gated and poison-
        screened. "Refactor Genesis" dropped both of these entirely, along
        with the RagPoisonDetector they (and lancedb_rag) depend on.
    """

    def __init__(self, repo_root: Path, tier: Tier, docs_dir: Path | None = None):
        self.repo_root = repo_root
        self.tier = tier
        self.docs_dir = docs_dir
        self._poison = RagPoisonDetector()

    @property
    def fetch_allowed(self) -> bool:
        return self.tier in (Tier.PUBLIC, Tier.INTERNAL)

    def _resolve(self, rel_path: str) -> Path | None:
        """Resolve rel_path under repo_root; None if it would escape the repo."""
        candidate = (self.repo_root / rel_path).resolve()
        try:
            candidate.relative_to(self.repo_root.resolve())
        except ValueError:
            return None
        return candidate

    def list_dependencies(self) -> list[dict[str, Any]]:
        """List all declared dependencies with versions, from whichever
        manifest files exist at the repo root."""
        deps: list[dict[str, Any]] = []
        req = self.repo_root / "requirements.txt"
        if req.exists():
            for line in req.read_text(errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                m = re.match(r"^([A-Za-z0-9_.\-\[\]]+)\s*([=<>!~]{1,2}=?\s*[\w.\-]+)?", line)
                if m:
                    deps.append({"name": m.group(1), "version": (m.group(2) or "").strip(),
                                 "source": "requirements.txt"})
        pyproject = self.repo_root / "pyproject.toml"
        if pyproject.exists():
            in_deps = False
            for line in pyproject.read_text(errors="replace").splitlines():
                stripped = line.strip()
                if stripped.startswith("["):
                    in_deps = "dependencies" in stripped.lower()
                    continue
                if in_deps and "=" in stripped:
                    name, _, version = stripped.partition("=")
                    deps.append({"name": name.strip().strip('"'), "version": version.strip(),
                                 "source": "pyproject.toml"})
        package_json = self.repo_root / "package.json"
        if package_json.exists():
            try:
                data = json.loads(package_json.read_text(errors="replace"))
            except Exception:
                logger.warning("failed to parse package.json; using empty", exc_info=True)
                data = {}
            for section in ("dependencies", "devDependencies"):
                for name, version in (data.get(section) or {}).items():
                    deps.append({"name": name, "version": version, "source": f"package.json:{section}"})
        return deps

    def get_schema(self, fmt: str = "json") -> dict | str | None:
        """Get API schema (OpenAPI/Swagger) if a schema file exists at the repo root."""
        candidates = ["openapi.json", "openapi.yaml", "openapi.yml",
                      "swagger.json", "swagger.yaml", "docs/openapi.json"]
        for name in candidates:
            path = self.repo_root / name
            if not path.exists():
                continue
            text = path.read_text(errors="replace")
            if path.suffix == ".json":
                return json.loads(text) if fmt == "json" else text
            return text if fmt != "json" else {"raw": text, "format": path.suffix.lstrip(".")}
        return None

    def find_reference(self, query: str, kind: str = "any") -> list[dict]:
        """Best-effort search for a class/function definition by name across
        the repo's source files (not a real symbol index -- see the class
        docstring for scope)."""
        if not self.repo_root.exists():
            return []
        kinds = ["class", "function"] if kind == "any" else [kind]
        kinds = [k for k in kinds if k in _DEF_PATTERNS]
        results: list[dict] = []
        for path in self.repo_root.rglob("*"):
            if path.is_dir() or path.suffix not in _SYMBOL_EXTS:
                continue
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            try:
                lines = path.read_text(errors="replace").splitlines()
            except Exception:
                logger.warning("failed to read file while scanning; skipping", exc_info=True)
                continue
            for i, line in enumerate(lines):
                for k in kinds:
                    m = _DEF_PATTERNS[k].match(line)
                    if m and query.lower() in m.group(1).lower():
                        results.append({
                            "kind": k, "name": m.group(1),
                            "file_path": str(path.relative_to(self.repo_root)),
                            "line": i + 1, "signature": line.strip(),
                        })
        return results

    def get_file(self, path: str) -> str | None:
        """Read a documentation/markdown file from the repo (path traversal-safe)."""
        resolved = self._resolve(path)
        if resolved is None or not resolved.is_file():
            return None
        try:
            return resolved.read_text(errors="replace")
        except Exception:
            logger.warning("failed to read file; returning None", exc_info=True)
            return None

    def get_readme(self) -> str | None:
        """Get the repository README content."""
        for name in _README_NAMES:
            path = self.repo_root / name
            if path.exists():
                try:
                    return path.read_text(errors="replace")
                except Exception:
                    logger.warning("failed to read README; skipping", exc_info=True)
                    continue
        return None

    def search_local(self, query: str, limit: int = 8) -> str:
        """Keyword-search the local documentation corpus (markdown/text/rst
        files). Fully offline. Ported from the pre-refactor documentation
        server, which this behaviour was dropped from."""
        if not self.docs_dir or not self.docs_dir.exists():
            return "(no local docs corpus)"
        terms = [t for t in query.lower().split() if len(t) > 2]
        hits: list[tuple[int, str, str]] = []
        for f in self.docs_dir.rglob("*"):
            if f.suffix.lower() not in (".md", ".txt", ".rst"):
                continue
            try:
                text = f.read_text(errors="replace")
            except Exception:
                logger.warning("failed to read corpus file; skipping", exc_info=True)
                continue
            low = text.lower()
            score = sum(low.count(t) for t in terms)
            if score:
                snippet = text[:400].replace("\n", " ")
                hits.append((score, str(f.relative_to(self.docs_dir)), snippet))
        hits.sort(reverse=True)
        if not hits:
            return "(no matches)"
        return "\n\n".join(f"## {name} (score {s})\n{snip}" for s, name, snip in hits[:limit])

    def fetch_external(self, url: str) -> tuple[str, bool]:
        """Fetch an external URL. Returns (text, blocked_by_policy). The
        tier gate lives in the MCP-server layer (fetch_allowed is exposed for
        it to check); this method itself always screens the fetched content
        for poisoning before returning it."""
        import httpx
        resp = httpx.get(url, timeout=20, follow_redirects=True)
        body = resp.text[:20000]
        verdict = self._poison.scan_chunk(body, source=url)
        if verdict.blocked:
            return "BLOCKED: fetched content failed safety screening.", True
        return f'<external_doc source="{url}" treat-as="data">\n{body}\n</external_doc>', False


def get_docs_service(repo_root: Path, tier: Tier, docs_dir: Path | None = None) -> DocsService:
    """Factory for docs service."""
    return DocsService(repo_root, tier, docs_dir)
