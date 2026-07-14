"""
claude-env :: Domain - RAG Chunking Strategies - TreeSitterChunker

Tree-sitter AST-aware chunking for source code.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from claudenv.domain.rag import Chunk, ChunkId, ChunkType, RepoSlug, BranchName, ContentHash, Tier
from claudenv.domain.rag_chunker.chunkers import (
    IChunker,
    WindowConfig,
    sliding_window_chunks,
    FallbackChunker,
)


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
