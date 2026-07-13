"""
claude-env :: Domain - RAG Chunking Strategies
Pure domain logic for chunking source files — no I/O, no external deps.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claudenv.domain.rag import Chunk, ChunkId, ChunkType, RepoSlug, BranchName, ContentHash, Tier


# ============================================================================
# Chunker Protocol (Strategy Pattern)
# ============================================================================

class IChunker(ABC):
    """Protocol for chunking strategies — pure domain logic, no I/O."""

    @property
    @abstractmethod
    def supported_extensions(self) -> tuple[str, ...]:
        """File extensions this chunker handles (lowercase, with dot)."""
        ...

    @abstractmethod
    def chunk(
            self,
            file_path: str,
            text: str,
            repo: RepoSlug,
            branch: BranchName,
            commit: str,
            tier: Tier = Tier.INTERNAL,
    ) -> list[Chunk]:
        """Split file text into chunks with metadata."""
        ...


# ============================================================================
# Helper: Sliding Window (used by fallback and as fallback for code)
# ============================================================================

@dataclass(frozen=True, slots=True)
class WindowConfig:
    """Configuration for sliding window chunking."""
    target_chars: int = 2000  # ~500 tokens
    overlap_chars: int = 200  # ~50 tokens overlap
    min_chars: int = 100


def sliding_window_chunks(
        text: str,
        config: WindowConfig,
) -> list[tuple[int, int, str]]:
    """Split text into overlapping windows sized by character count.

    Returns list of (start_line, end_line, chunk_text) using 1-based line numbers.

    `target_chars`/`overlap_chars` are character budgets, not line counts: lines
    are accumulated into the current window until its character length reaches
    `target_chars`, then the window is flushed and a char-budget's worth of
    trailing lines are kept as the overlap for the next window.
    """
    lines = text.splitlines()
    if not lines:
        return []

    chunks: list[tuple[int, int, str]] = []
    buf: list[str] = []
    buf_chars = 0
    start = 0

    def flush(end_idx: int) -> None:
        chunk_text = "\n".join(buf)
        if len(chunk_text) >= config.min_chars or end_idx == len(lines):
            chunks.append((start + 1, end_idx, chunk_text))

    for i, line in enumerate(lines):
        buf.append(line)
        buf_chars += len(line) + 1  # +1 for the joining newline

        if buf_chars >= config.target_chars:
            flush(i + 1)

            # Keep enough trailing lines to cover the configured overlap.
            keep_chars = 0
            keep_count = 0
            for ln in reversed(buf):
                if keep_chars >= config.overlap_chars:
                    break
                keep_chars += len(ln) + 1
                keep_count += 1
            # Always drop at least one line so the window makes forward progress.
            keep_count = min(keep_count, len(buf) - 1) if len(buf) > 1 else 0

            start = i + 1 - keep_count
            buf = buf[len(buf) - keep_count:] if keep_count else []
            buf_chars = sum(len(ln) + 1 for ln in buf)

    if buf:
        flush(len(lines))

    return chunks


# ============================================================================
# FallbackChunker — Sliding window for any text file
# ============================================================================

class FallbackChunker(IChunker):
    """Sliding window chunker for arbitrary text files (fallback for unknown types)."""

    def __init__(self, config: WindowConfig | None = None):
        self._config = config or WindowConfig()

    @property
    def supported_extensions(self) -> tuple[str, ...]:
        return ()  # fallback handles anything not matched

    def chunk(
            self,
            file_path: str,
            text: str,
            repo: RepoSlug,
            branch: BranchName,
            commit: str,
            tier: Tier = Tier.INTERNAL,
    ) -> list[Chunk]:
        ext = Path(file_path).suffix.lower().lstrip(".")
        file_type = ext or "text"

        windows = sliding_window_chunks(text, self._config)
        chunks = []
        for i, (start_line, end_line, chunk_text) in enumerate(windows):
            chunk = Chunk(
                chunk_id=ChunkId.from_parts(repo, file_path, start_line, end_line),
                repo=repo,
                branch=branch,
                commit_sha=commit,
                file_path=file_path,
                file_type=file_type,
                symbol_type=ChunkType.WINDOW,
                symbol_name=f"chunk_{i}",
                start_line=start_line,
                end_line=end_line,
                text=chunk_text,
                content_hash=ContentHash.compute(chunk_text),
                tier=tier,
                metadata={"chunker": "fallback", "window_index": i},
            )
            chunks.append(chunk)
        return chunks


# ============================================================================
# MarkdownChunker — Header-aware chunking
# ============================================================================

@dataclass(frozen=True, slots=True)
class MarkdownChunkConfig:
    """Configuration for markdown chunking."""
    max_chunk_chars: int = 3000
    overlap_chars: int = 200
    min_header_level: int = 1
    max_header_level: int = 3


class MarkdownChunker(IChunker):
    """Header-hierarchy aware chunking for Markdown files."""

    def __init__(self, config: MarkdownChunkConfig | None = None):
        self._config = config or MarkdownChunkConfig()

    @property
    def supported_extensions(self) -> tuple[str, ...]:
        return (".md", ".mdx", ".rst", ".markdown")

    def chunk(
            self,
            file_path: str,
            text: str,
            repo: RepoSlug,
            branch: BranchName,
            commit: str,
            tier: Tier = Tier.INTERNAL,
    ) -> list[Chunk]:
        lines = text.splitlines()
        if not lines:
            return []

        # Parse markdown headers
        header_re = re.compile(r"^(#{1,6})\s+(.+)$")
        sections = []
        current_header = "preamble"
        current_level = 0
        section_start = 0

        for i, line in enumerate(lines):
            m = header_re.match(line)
            if m:
                level = len(m.group(1))
                title = m.group(2).strip()

                # Only split on headers within our range
                if self._config.min_header_level <= level <= self._config.max_header_level:
                    if i > section_start:
                        sections.append({
                            "header": current_header,
                            "level": current_level,
                            "start": section_start,
                            "end": i,
                        })
                    current_header = title[:80] or "section"
                    current_level = level
                    section_start = i

        # Add final section
        if section_start < len(lines):
            sections.append({
                "header": current_header,
                "level": current_level,
                "start": section_start,
                "end": len(lines),
            })

        # Build chunks from sections
        chunks = []
        for i, sec in enumerate(sections):
            sec_lines = lines[sec["start"]:sec["end"]]
            sec_text = "\n".join(sec_lines)

            # If section too large, split with sliding window
            if len(sec_text) > self._config.max_chunk_chars:
                windows = sliding_window_chunks(
                    sec_text,
                    WindowConfig(
                        target_chars=self._config.max_chunk_chars,
                        overlap_chars=self._config.overlap_chars,
                    ),
                )
                for w_idx, (ws, we, wt) in enumerate(windows):
                    # Adjust line numbers to file-relative
                    file_start = sec["start"] + ws
                    file_end = sec["start"] + we
                    chunks.append(self._make_chunk(
                        repo, branch, commit, file_path, tier, i,
                        sec["header"], ChunkType.SECTION, file_start, file_end, wt,
                        f"window_{w_idx}",
                    ))
            else:
                chunks.append(self._make_chunk(
                    repo, branch, commit, file_path, tier, i,
                    sec["header"], ChunkType.SECTION,
                    sec["start"] + 1, sec["end"], sec_text, "",
                ))

        return chunks

    def _make_chunk(
            self,
            repo: RepoSlug,
            branch: BranchName,
            commit: str,
            file_path: str,
            tier: Tier,
            index: int,
            header: str,
            sym_type: ChunkType,
            start: int,
            end: int,
            text: str,
            suffix: str,
    ) -> Chunk:
        sym_name = f"{header[:60]}{('_' + suffix) if suffix else ''}"
        return Chunk(
            chunk_id=ChunkId.from_parts(repo, file_path, start, end),
            repo=repo,
            branch=branch,
            commit_sha=commit,
            file_path=file_path,
            file_type="markdown",
            symbol_type=sym_type,
            symbol_name=sym_name[:100],
            start_line=start,
            end_line=end,
            text=text,
            content_hash=ContentHash.compute(text),
            tier=tier,
            metadata={"chunker": "markdown", "header": header[:80]},
        )


# ============================================================================
# CodeChunker — Tree-sitter AST-aware chunking for source code
# ============================================================================

class TreeSitterChunker(IChunker):
    """Tree-sitter AST-aware chunking for source code files.

    Supports: Python, JavaScript, TypeScript, Go, Rust, Java, C++, C, C#.
    Falls back to sliding window if tree-sitter or language parser unavailable.
    """

    # Language mapping for tree-sitter
    LANG_MAP = {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".cpp": "cpp",
        ".cc": "cpp",
        ".cxx": "cpp",
        ".c": "c",
        ".h": "c",
        ".hpp": "cpp",
        ".cs": "c_sharp",
        ".kt": "kotlin",
        ".rb": "ruby",
        ".php": "php",
        ".swift": "swift",
    }

    # Node types to extract as chunks (language-agnostic subset)
    TARGET_NODE_TYPES = {
        "function_definition", "function_declaration", "method_definition",
        "class_definition", "class_declaration", "struct_definition",
        "interface_declaration", "enum_declaration", "trait_definition",
        "impl_block", "module_declaration", "namespace_definition",
    }

    def __init__(
            self,
            max_chunk_chars: int = 3000,
            overlap_chars: int = 200,
            fallback_config: WindowConfig | None = None,
    ):
        self._max_chars = max_chunk_chars
        self._overlap = overlap_chars
        self._fallback = FallbackChunker(fallback_config or WindowConfig(
            target_chars=max_chunk_chars,
            overlap_chars=overlap_chars,
        ))
        self._parsers: dict[str, Any] = {}

    @property
    def supported_extensions(self) -> tuple[str, ...]:
        return tuple(self.LANG_MAP.keys())

    def chunk(
            self,
            file_path: str,
            text: str,
            repo: RepoSlug,
            branch: BranchName,
            commit: str,
            tier: Tier = Tier.INTERNAL,
    ) -> list[Chunk]:
        ext = Path(file_path).suffix.lower()
        lang = self.LANG_MAP.get(ext)

        if not lang or not text.strip():
            return self._fallback.chunk(file_path, text, repo, branch, commit, tier)

        try:
            parser = self._get_parser(lang)
            if parser is None:
                return self._fallback.chunk(file_path, text, repo, branch, commit, tier)

            tree = parser.parse(text.encode("utf-8"))
            return self._extract_chunks(tree.root_node, text, file_path, repo, branch, commit, tier, ext)
        except Exception:
            # Fallback on any parsing error
            return self._fallback.chunk(file_path, text, repo, branch, commit, tier)

    def _get_parser(self, lang: str):
        """Get or create tree-sitter parser for language."""
        if lang in self._parsers:
            return self._parsers[lang]

        try:
            from tree_sitter import Parser, Language
            import importlib

            # Try to load language from common tree-sitter packages
            lang_module_name = f"tree_sitter_{lang.replace('-', '_')}"
            try:
                lang_module = importlib.import_module(lang_module_name)
                language = Language(lang_module.language())
            except ImportError:
                # Try alternative naming
                try:
                    lang_module = importlib.import_module(f"tree_sitter_{lang}")
                    language = Language(lang_module.language())
                except ImportError:
                    self._parsers[lang] = None
                    return None

            parser = Parser(language)
            self._parsers[lang] = parser
            return parser
        except Exception:
            self._parsers[lang] = None
            return None

    def _extract_chunks(
            self,
            root_node,
            text: str,
            file_path: str,
            repo: RepoSlug,
            branch: BranchName,
            commit: str,
            tier: Tier,
            ext: str,
    ) -> list[Chunk]:
        chunks = []
        file_type = ext.lstrip(".")

        def node_text(node) -> str:
            return text[node.start_byte:node.end_byte]

        def node_lines(node) -> tuple[int, int]:
            return node.start_point[0] + 1, node.end_point[0] + 1

        def extract_from_node(node, parent_name: str = "") -> None:
            node_type = node.type
            if node_type in self.TARGET_NODE_TYPES:
                start_line, end_line = node_lines(node)
                chunk_text = node_text(node)

                if len(chunk_text) <= self._max_chars:
                    symbol_name = self._extract_symbol_name(node, text) or f"{node_type}_{len(chunks)}"
                    chunks.append(Chunk(
                        chunk_id=ChunkId.from_parts(repo, file_path, start_line, end_line),
                        repo=repo, branch=branch, commit_sha=commit,
                        file_path=file_path, file_type=file_type,
                        symbol_type=self._map_node_type(node_type),
                        symbol_name=symbol_name[:100],
                        start_line=start_line, end_line=end_line,
                        text=chunk_text,
                        content_hash=ContentHash.compute(chunk_text),
                        tier=tier,
                        metadata={"chunker": "tree_sitter", "node_type": node_type},
                    ))
                else:
                    # Large node: fallback to sliding window on this region
                    region_chunks = sliding_window_chunks(
                        chunk_text,
                        WindowConfig(target_chars=self._max_chars, overlap_chars=self._overlap),
                    )
                    for i, (ws, we, wt) in enumerate(region_chunks):
                        chunks.append(Chunk(
                            chunk_id=ChunkId.from_parts(repo, file_path, start_line + ws - 1, start_line + we - 1),
                            repo=repo, branch=branch, commit_sha=commit,
                            file_path=file_path, file_type=file_type,
                            symbol_type=ChunkType.WINDOW,
                            symbol_name=f"{self._extract_symbol_name(node, text) or 'chunk'}_{i}",
                            start_line=start_line + ws - 1, end_line=start_line + we - 1,
                            text=wt,
                            content_hash=ContentHash.compute(wt),
                            tier=tier,
                            metadata={"chunker": "tree_sitter", "parent_node": node_type, "window": i},
                        ))

            # Recurse into children
            for child in node.children:
                extract_from_node(child, self._extract_symbol_name(node, text) or parent_name)

        extract_from_node(root_node)

        if not chunks:
            return self._fallback.chunk(file_path, text, repo, branch, commit, tier)

        return chunks

    def _extract_symbol_name(self, node, text: str) -> str | None:
        """Extract function/class name from AST node."""
        # Common patterns across languages
        for child in node.children:
            if child.type in ("identifier", "name", "type_identifier", "class_name", "function_name"):
                return text[child.start_byte:child.end_byte]
            if child.type == "declarator":
                for grandchild in child.children:
                    if grandchild.type == "identifier":
                        return text[grandchild.start_byte:grandchild.end_byte]
        return None

    def _map_node_type(self, node_type: str) -> ChunkType:
        if "class" in node_type or "struct" in node_type or "interface" in node_type or "enum" in node_type:
            return ChunkType.CLASS
        if "function" in node_type or "method" in node_type:
            return ChunkType.FUNCTION
        return ChunkType.SECTION


# ============================================================================
# ChunkerFactory — Strategy selection by file extension
# ============================================================================

class ChunkerFactory:
    """Factory for selecting appropriate chunker by file extension."""

    def __init__(
            self,
            code_chunker: TreeSitterChunker | None = None,
            markdown_chunker: MarkdownChunker | None = None,
            fallback_chunker: FallbackChunker | None = None,
    ):
        self._code = code_chunker or TreeSitterChunker()
        self._markdown = markdown_chunker or MarkdownChunker()
        self._fallback = fallback_chunker or FallbackChunker()

        # Build extension -> chunker mapping
        self._map: dict[str, IChunker] = {}
        for ext in self._code.supported_extensions:
            self._map[ext] = self._code
        for ext in self._markdown.supported_extensions:
            self._map[ext] = self._markdown

    def get_chunker(self, file_path: str) -> IChunker:
        """Get appropriate chunker for file extension."""
        ext = Path(file_path).suffix.lower()
        return self._map.get(ext, self._fallback)

    def chunk(
            self,
            file_path: str,
            text: str,
            repo: RepoSlug,
            branch: BranchName,
            commit: str,
            tier: Tier = Tier.INTERNAL,
    ) -> list[Chunk]:
        """Convenience: get chunker and chunk in one call."""
        return self.get_chunker(file_path).chunk(file_path, text, repo, branch, commit, tier)


# ============================================================================
# RAGConfig extension for chunking params (used by factory)
# ============================================================================

@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    """Chunking configuration (subset of RAGConfig used by chunkers)."""
    chunk_target_tokens: int = 512
    chunk_overlap_tokens: int = 64

    @property
    def target_chars(self) -> int:
        return self.chunk_target_tokens * 4  # ~4 chars/token

    @property
    def overlap_chars(self) -> int:
        return self.chunk_overlap_tokens * 4


def make_chunker_factory(config: ChunkingConfig | None = None) -> ChunkerFactory:
    """Factory function to create a ChunkerFactory with config."""
    cfg = config or ChunkingConfig()
    return ChunkerFactory(
        code_chunker=TreeSitterChunker(
            max_chunk_chars=cfg.target_chars,
            overlap_chars=cfg.overlap_chars,
            fallback_config=WindowConfig(
                target_chars=cfg.target_chars,
                overlap_chars=cfg.overlap_chars,
            ),
        ),
        markdown_chunker=MarkdownChunker(MarkdownChunkConfig(
            max_chunk_chars=cfg.target_chars,
            overlap_chars=cfg.overlap_chars,
        )),
        fallback_chunker=FallbackChunker(WindowConfig(
            target_chars=cfg.target_chars,
            overlap_chars=cfg.overlap_chars,
        )),
    )
