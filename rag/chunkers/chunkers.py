"""
claude-env :: chunkers
File: rag/chunkers/chunkers.py
Purpose:
    Turn a file into semantically coherent chunks with metadata. Strategy is
    chosen by file type:
      * code      -> AST-aware (tree-sitter) split at function/class boundaries
      * markdown  -> header-hierarchy split
      * adr/rfc   -> section split on '## '
      * fallback  -> sliding window
    Token counts are approximated as len(text)//4 unless a real tokenizer is
    injected. Targets/overlaps come from rag/embeddings reasoning.

Responsibilities:
    Chunk: dataclass carrying text + metadata used by the indexer.
    chunk_file(path, text, repo, branch, commit) -> list[Chunk]

tree-sitter grammars (install via pip): tree_sitter_language_pack bundles
python, javascript, typescript, go, java, kotlin, c_sharp, rust, c, cpp.
(Successor to the abandoned tree_sitter_languages; same get_parser() API.)
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, asdict
from pathlib import Path

try:
    from tree_sitter import Parser as _TSParser
    from tree_sitter_language_pack import get_language as _get_language
except ImportError:  # pragma: no cover
    _TSParser = _get_language = None

_PARSER_CACHE: dict = {}


def get_parser(lang: str):
    """Return a cached tree_sitter.Parser for `lang`, or None if unavailable.

    Built from the stable core `tree_sitter.Parser` + a grammar from
    tree_sitter_language_pack. We deliberately do NOT use the pack's own
    `get_parser()`: its bundled parser has a divergent API (parse() expects a
    str and raises on bytes; root_node is a method, not a property) that breaks
    the byte-offset AST walk below. Unknown/unsupported langs cache as None and
    fall back to window chunking.
    """
    if _TSParser is None or _get_language is None:
        return None
    if lang not in _PARSER_CACHE:
        try:
            _PARSER_CACHE[lang] = _TSParser(_get_language(lang))
        except Exception:
            _PARSER_CACHE[lang] = None
    return _PARSER_CACHE[lang]

TARGETS = {  # (target_tokens, overlap_tokens)
    "code": (512, 64),
    "markdown": (384, 48),
    "section": (256, 32),
    "fallback": (400, 50),
}

EXT_LANG = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "tsx",
    ".go": "go", ".java": "java", ".kt": "kotlin", ".cs": "c_sharp",
    ".rs": "rust", ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp",
}

# tree-sitter node types that delimit a "unit" per language
UNIT_NODES = {
    "python": {"function_definition", "class_definition"},
    "javascript": {"function_declaration", "class_declaration", "method_definition"},
    "typescript": {"function_declaration", "class_declaration", "method_definition"},
    "tsx": {"function_declaration", "class_declaration", "method_definition"},
    "go": {"function_declaration", "method_declaration", "type_declaration"},
    "java": {"method_declaration", "class_declaration", "interface_declaration"},
    "kotlin": {"function_declaration", "class_declaration", "object_declaration"},
    "c_sharp": {"method_declaration", "class_declaration", "interface_declaration"},
    "rust": {"function_item", "impl_item", "struct_item", "enum_item"},
    "c": {"function_definition", "struct_specifier"},
    "cpp": {"function_definition", "class_specifier", "struct_specifier"},
}


def approx_tokens(s: str) -> int:
    return max(1, len(s) // 4)


@dataclass
class Chunk:
    chunk_id: str
    repo: str
    branch: str
    commit_sha: str
    file_path: str
    file_type: str
    symbol_type: str          # function|class|section|window
    symbol_name: str
    start_line: int
    end_line: int
    text: str
    content_hash: str

    def as_metadata(self) -> dict:
        d = asdict(self)
        return d


def _cid(repo: str, path: str, start: int, end: int) -> str:
    return hashlib.sha1(f"{repo}:{path}:{start}:{end}".encode()).hexdigest()


def _mk(repo, branch, commit, path, ftype, stype, name, s, e, text) -> Chunk:
    return Chunk(
        chunk_id=_cid(repo, path, s, e), repo=repo, branch=branch,
        commit_sha=commit, file_path=path, file_type=ftype, symbol_type=stype,
        symbol_name=name, start_line=s, end_line=e, text=text,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
    )


def _split_window(text: str, target: int, overlap: int) -> list[tuple[int, int, str]]:
    lines = text.splitlines()
    out, buf, start = [], [], 0
    tok = 0
    for i, ln in enumerate(lines):
        buf.append(ln)
        tok += approx_tokens(ln)
        if tok >= target:
            seg = "\n".join(buf)
            out.append((start + 1, i + 1, seg))
            keep = max(0, int(len(buf) * overlap / target))
            buf = buf[len(buf) - keep:] if keep else []
            start = i - keep
            tok = sum(approx_tokens(x) for x in buf)
    if buf:
        out.append((start + 1, len(lines), "\n".join(buf)))
    return out


def _chunk_code(text: str, lang: str) -> list[tuple[str, str, int, int, str]]:
    """Return [(symbol_type, symbol_name, start, end, text)]."""
    parser = get_parser(lang)
    if parser is None:
        # no AST available (deps missing or unsupported language) -> window split
        return [("window", "window", s, e, t)
                for s, e, t in _split_window(text, *TARGETS["code"])]
    try:
        tree = parser.parse(text.encode())
    except Exception:
        return [("window", "window", s, e, t)
                for s, e, t in _split_window(text, *TARGETS["code"])]
    units = UNIT_NODES.get(lang, set())
    src = text.encode()
    out: list[tuple[str, str, int, int, str]] = []

    def name_of(node) -> str:
        for ch in node.children:
            if ch.type in ("identifier", "name", "type_identifier"):
                return src[ch.start_byte:ch.end_byte].decode(errors="ignore")
        return node.type

    def walk(node):
        if node.type in units:
            seg = src[node.start_byte:node.end_byte].decode(errors="ignore")
            stype = "class" if "class" in node.type or "struct" in node.type \
                or "impl" in node.type else "function"
            # large units get sub-windowed to respect token budget
            if approx_tokens(seg) > TARGETS["code"][0] * 1.5:
                for s, e, t in _split_window(seg, *TARGETS["code"]):
                    out.append((stype, name_of(node),
                                node.start_point[0] + s, node.start_point[0] + e, t))
            else:
                out.append((stype, name_of(node),
                            node.start_point[0] + 1, node.end_point[0] + 1, seg))
            return  # don't descend into already-captured unit
        for ch in node.children:
            walk(ch)

    walk(tree.root_node)
    if not out:  # no recognized units (e.g. a config-ish file) -> window
        return [("window", "window", s, e, t)
                for s, e, t in _split_window(text, *TARGETS["code"])]
    return out


def _chunk_markdown(text: str) -> list[tuple[str, str, int, int, str]]:
    lines = text.splitlines()
    out, buf, header, start = [], [], "preamble", 0
    target, overlap = TARGETS["markdown"]
    tok = 0

    def flush(end):
        if buf:
            out.append(("section", header, start + 1, end, "\n".join(buf)))

    for i, ln in enumerate(lines):
        if ln.startswith("#"):
            flush(i)
            header = ln.lstrip("# ").strip()[:80] or "section"
            buf, start, tok = [ln], i, approx_tokens(ln)
            continue
        buf.append(ln)
        tok += approx_tokens(ln)
        if tok >= target:
            flush(i + 1)
            buf, start, tok = [], i, 0
    flush(len(lines))
    return out


def chunk_file(path: str, text: str, repo: str, branch: str,
               commit_sha: str) -> list[Chunk]:
    ext = Path(path).suffix.lower()
    chunks: list[Chunk] = []
    if ext in (".md", ".mdx", ".rst"):
        parts = _chunk_markdown(text)
        ftype = "markdown"
    elif ext in EXT_LANG:
        parts = _chunk_code(text, EXT_LANG[ext])
        ftype = EXT_LANG[ext]
    else:
        parts = [("window", "window", s, e, t)
                 for s, e, t in _split_window(text, *TARGETS["fallback"])]
        ftype = ext.lstrip(".") or "text"
    for stype, name, s, e, t in parts:
        if t.strip():
            chunks.append(_mk(repo, branch, commit_sha, path, ftype,
                              stype, name, s, e, t))
    return chunks
