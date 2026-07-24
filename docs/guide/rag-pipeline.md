# RAG Pipeline — Config, Chunking, Embedding, Reranking, Storage

> Relates to: [OVERVIEW.md §5 — knowledge and search shouldn't leave the
> building](../OVERVIEW.md#5-knowledge-and-search-shouldnt-leave-the-building)

**Source:** [`claudenv/domain/rag/config.py`](../../claudenv/domain/rag/config.py),
[`claudenv/domain/rag_chunker/chunkers.py`](../../rag/chunkers/chunkers.py),
[`claudenv/adapters/embedding/embedders.py`](../../rag/embeddings/base.py),
[`claudenv/adapters/embedding/factory.py`](../../rag/embeddings/registry.py),
[`claudenv/adapters/embedding/embedders.py`](../../rag/embeddings/llama_embedder.py),
[`claudenv/adapters/embedding/rerankers.py`](../../rag/rerankers/base.py),
[`claudenv/adapters/embedding/factory.py`](../../rag/rerankers/registry.py),
[`claudenv/adapters/embedding/rerankers.py`](../../rag/rerankers/cross_encoder.py),
[`claudenv/adapters/vector/lancedb/vector_store.py`](../../claudenv/adapters/vector/lancedb/vector_store.py).
**Config:** [`claudenv/_data/config/rag.yaml`](../../config/rag.yaml).

This doc covers the modules that turn a repo's files into a searchable local index:
model configuration, chunking, embedding, reranking, and the LanceDB store. It does
**not** cover how a query is screened, fused, and returned end-to-end — that's
[`retrieval-poison-screening.md`](retrieval-poison-screening.md), which owns
`claudenv/application/rag/service.py`. It also doesn't cover the six MCP servers (including
`lancedb-rag`) that expose these modules to agents — that's `mcp-servers.md`.

---

## What it does (30-second version)

A file becomes searchable in four steps: `chunk_file()` splits it into
semantically coherent pieces (AST units for code, header sections for markdown, a
sliding window otherwise); the configured `EmbedderBackend` (`LlamaEmbedder` by
default) turns each chunk's text into a normalized vector; `LanceStore` upserts the
vectors + metadata into a per-`(repo, branch)` LanceDB table on local disk; at query
time the same store runs a hybrid vector+FTS search and the configured
`RerankerBackend` (`CrossEncoderReranker` by default) re-orders the top candidates.
Every model **and backend** is configured — never hardcoded — in `claudenv/_data/config/rag.yaml`
or an env var, resolved by `claudenv/domain/rag/config.py`. Nothing leaves the machine.

Both the embedder and reranker sit behind a small interface + registry
(`claudenv/adapters/embedding/embedders.py`+`registry.py`, `claudenv/adapters/embedding/rerankers.py`+`registry.py`) so a
new backend — a different runtime, not just a different model file — can be added
by implementing the interface and registering it, with zero changes to
`claudenv/domain/rag/config.py`'s factories or any caller. See
["Swapping backends, not just models"](#swapping-backends-not-just-models) below.

---

## Configuration reference (`claudenv/_data/config/rag.yaml`)

Resolution order for every field (highest priority first): **1. environment
variable → 2. `claudenv/_data/config/rag.yaml` → 3. built-in numeric fallback.** Model *paths*
have no fallback — they default to `""` and the code fails loud rather than
guessing (`claudenv/domain/rag/config.py`).

| Key | Env override | Type | Built-in fallback | Effect |
|---|---|---|---|---|
| `embedding.backend` | `EMBED_BACKEND` | str | `"llama_cpp"` | Registry key (`claudenv/adapters/embedding/factory.py`) selecting which `EmbedderBackend` implementation to construct. Unknown key raises `ValueError` listing valid keys. |
| `embedding.model_path` | `EMBED_MODEL_PATH` | str | `""` (none) | Path to a local GGUF embedding model. Empty means unconfigured; `get_embedder()` raises `RuntimeError` rather than picking a default. |
| `embedding.model_name` | `EMBED_MODEL_NAME` | str | derived | Label stored in `rag_index_state.embed_model`. If unset, derived as `Path(model_path).stem` (`claudenv/domain/rag/config.py`); empty path → empty name. |
| `embedding.document_prefix` | `EMBED_DOC_PREFIX` | str | `""` | Prepended verbatim to text before embedding a document (some models need a task prefix, e.g. nomic-embed-text's `"search_document: "`). |
| `embedding.query_prefix` | `EMBED_QUERY_PREFIX` | str | `""` | Prepended verbatim to text before embedding a query. |
| `embedding.pooling_type` | `EMBED_POOLING_TYPE` | str | `"mean"` | One of `mean\|cls\|last\|none`, passed to llama.cpp's `pooling_type`. Wrong for the model → `embed_documents()`/`embed_query()` returns one vector *per token* instead of one pooled vector per input; `_embed()` detects this shape and raises `TypeError` rather than silently normalizing the wrong thing. |
| `embedding.n_ctx` | `EMBED_CTX` | int | `2048` | llama.cpp context window in tokens. |
| `embedding.n_gpu_layers` | `EMBED_GPU_LAYERS` | int | `-1` | `-1` offloads all layers to Metal on Apple Silicon; `0` forces CPU only. |
| `embedding.embedding_dim` | `EMBED_DIM` | int | `768` | Output vector dimension; must match the configured model. Changing it requires a full re-index (it's baked into the LanceDB table schema). |
| `reranker.backend` | `RERANKER_BACKEND` | str | `"onnx_cross_encoder"` | Registry key (`claudenv/adapters/embedding/factory.py`) selecting which `RerankerBackend` implementation to construct. |
| `reranker.model_dir` | `RERANKER_DIR` | str | `""` (disabled) | Directory containing an ONNX cross-encoder (`model.onnx` + tokenizer files). Empty disables reranking; the pipeline falls back to fusion order. |
| `RAG_CONFIG_YAML` | (this **is** the env var) | path | — | Explicit override for where `rag.yaml` itself is read from; see resolution order below. |
| `LANCEDB_PATH` | (this **is** the env var) | path | `~/.claude-env/knowledge/lancedb` | Where LanceDB tables live on disk (`claudenv/adapters/vector/lancedb/vector_store.py`). |

Where `claudenv/_data/config/rag.yaml` itself is found (`_resolve_yaml_path`, `claudenv/domain/rag/config.py`):

1. `RAG_CONFIG_YAML` env var, if set (`~` and env vars expanded).
2. `<repo_root>/config/rag.yaml` — one level up from `claudenv/domain/rag/config.py`, i.e. this
   works identically whether running from the platform repo or the mirrored
   `$CLAUDE_ENV_HOME` tree.
3. `$CLAUDE_ENV_HOME/config/rag.yaml` (defaults to `~/.claude-env/config/rag.yaml`).
4. If none exist, falls back to option 2's path anyway (so a well-defined path is
   always returned, and `_load_yaml` just returns `{}` for a missing file).

![Model resolution order](../assets/guide/rag-pipeline/model-resolution-order.svg)

---

## How to use it

```bash
# Sanity-check what would be indexed before running a full index — no model
# load, just policy-allowed vs. blocked file paths
claude-env scan /abs/path/to/my-service

# Build the full RAG index for the repo (loads the configured embedder,
# chunks every scanned file, upserts into the repo's LanceDB table)
claude-env index /abs/path/to/my-service

# Incrementally re-index just the files that changed since a given git ref
# (internally runs `git diff --name-only <ref> HEAD` to build the file list)
claude-env reindex /abs/path/to/my-service --since HEAD~1

# Incrementally re-index specific files by name instead
claude-env reindex /abs/path/to/my-service src/app.py docs/guide/rag-pipeline.md
```

`scan`, `index`, and `reindex` are raw `sys.argv` scripts, not `argparse` — they
take **no flags beyond what's shown above** (a bare positional `repo_root` for
`scan`/`index`; `repo_root` plus either a variadic file list or `--since <ref>`
for `reindex`). There's no `--help` output beyond a one-line usage string, so
this section is the actual reference for their arguments. Running `scan` before
the first `index` is worth doing on any new repo — it surfaces policy-driven
surprises (a large vendored directory that isn't actually excluded, say) before
paying the cost of loading the embedding model.

---

## Automatic re-indexing (git hooks)

**Source:** [`claudenv/application/rag/git_sync.py`](../../rag/git_sync.py),
[`claudenv/_data/scripts/post-commit`](../../scripts/post-commit),
[`claudenv/_data/scripts/post-merge`](../../scripts/post-merge),
[`claudenv/_data/scripts/post-checkout`](../../scripts/post-checkout).

Running `reindex` by hand is one way to keep the index current; the other is to not
have to. `claude-env onboard`/`register` installs three git hooks per repo (see
[`onboarding.md`](onboarding.md) for the install mechanics and the `--no-post-commit`
flag that skips all three) that dispatch through `claudenv/application/rag/git_sync.py` to the same
`Indexer.incremental()`/`Indexer.full_index()` this doc's `reindex`/`index` commands
call — no separate indexing logic, only new triggers:

| Git event | Hook | What runs |
|---|---|---|
| `git commit` | `post-commit` | `Indexer.incremental()` on the files changed in that commit. |
| `git merge` (including a `git pull`'s fast-forward, which never invokes `git commit`) | `post-merge` | `Indexer.incremental()` on the files changed between `ORIG_HEAD` and `HEAD`. |
| `git checkout <branch>` | `post-checkout` | A branch never indexed before gets a full `Indexer.full_index()`; a previously-indexed branch gets an `Indexer.incremental()` catch-up on the commits made since its recorded `rag_index_state.last_commit` — falling back to a full re-index instead if that commit is no longer in history (e.g. after a rebase/force-push). Ignored for a plain file-level checkout (git's own `is_branch_flag` argument distinguishes the two). |

Every hook runs in the background (`nohup`, logging to `logs/incremental_index.log`)
and never blocks or fails the triggering git command. A per-`(repo, branch)` lock file
under `state/locks/` prevents two triggers landing close together (e.g. an interactive
rebase firing `post-commit` several times in a row) from each loading the embedding
model concurrently — a later trigger simply skips with a logged note instead of piling
up redundant work.

---

## `claudenv/domain/rag/config.py` — the one place models get chosen

```python
# claudenv/domain/rag/config.py
def get_embedder(cfg: RagConfig | None = None):
    global _EMBEDDER, _EMBEDDER_KEY
    from rag.embeddings.registry import get_backend_class
    cfg = cfg or get_config()
    if not cfg.embedding.model_path:
        raise RuntimeError(
            "No embedding model configured. Set `embedding.model_path` in "
            "config/rag.yaml (or the EMBED_MODEL_PATH env var) to point at a local "
            "model file. See README §4 'Install local models' for download "
            "instructions and suggested models.")
    key = (cfg.embedding.backend, cfg.embedding.model_path)
    if _EMBEDDER is None or _EMBEDDER_KEY != key:
        backend_cls = get_backend_class(cfg.embedding.backend)
        _EMBEDDER = backend_cls.from_config(cfg.embedding)
        _EMBEDDER_KEY = key
        _audit_model_load("embedder", _EMBEDDER.info())
    return _EMBEDDER
```

`get_embedder()` and `get_reranker()` (`claudenv/domain/rag/config.py`) are the **only**
sanctioned way to construct these classes — every caller (indexer, retriever,
memory writes) goes through them, so a model **or backend** swap is a one-line
yaml/env change, never a code change. Both instances are cached at module level,
keyed by `(backend, model_path)` for the embedder (`_EMBEDDER`/`_EMBEDDER_KEY`) and
`(backend, model_dir)` for the reranker (`_RERANKER`/`_RERANKER_KEY`) — including
`backend` in the key (not just the path) means switching backends while the path
happens to stay the same still rebuilds rather than silently reusing a stale
instance of the wrong class. `RagConfig.load()` itself is memoized too (`_CONFIG`
singleton, `get_config()`, `claudenv/domain/rag/config.py`) and only re-reads yaml when called with
`reload=True`.

Every successful load (or reload) is recorded via `_audit_model_load()` →
`AuditLogger("rag-config", actor="rag_factory").agent_action(...)`, so which
model/backend was active for a given indexing or retrieval run is traceable in the
`agent_actions` audit projection — logging failures are swallowed (best-effort;
must never block RAG from working).

### Swapping backends, not just models

Historically "swap the model" meant "point `model_path` at a different GGUF" —
still true, and still the common case (see §4 in the README). But swapping the
**backend** — e.g. adding a non-llama.cpp embedder, or a reranker that isn't the
ONNX cross-encoder — used to mean editing `claudenv/domain/rag/config.py`'s factory functions
directly. It no longer does:

- `claudenv/adapters/embedding/embedders.py` defines `EmbedderBackend` (`embed_documents`,
  `embed_query`, `dim`, `model_name`, `backend_name`, `info()`); `LlamaEmbedder`
  is the one built-in implementation.
- `claudenv/adapters/embedding/rerankers.py` defines `RerankerBackend` (`rerank`, `ok`, `model_name`,
  `backend_name`, `status()`); `CrossEncoderReranker` is the one built-in
  implementation.
- `claudenv/adapters/embedding/factory.py` / `claudenv/adapters/embedding/factory.py` each hold a
  `dict[str, type[...Backend]]` (`BACKENDS`) mapping the config string
  (`embedding.backend` / `reranker.backend`) to the class. `get_backend_class()`
  raises a clear `ValueError` listing the valid keys if the configured one isn't
  registered.

To add a backend: implement the interface, add one line to the registry dict. No
changes to `claudenv/domain/rag/config.py`'s factories, and no changes to any caller (indexer,
retriever, memory) — they only ever call `get_embedder()`/`get_reranker()` and use
the interface's methods. See the `rag-model-setup` skill
(`.claude/skills/rag-model-setup/SKILL.md`) for the checklist to follow when
adding or switching a model, including how to probe a GGUF's real output
dimension and required `pooling_type` before touching `rag.yaml`.

### Hot-reload without a process restart

```python
# claudenv/domain/rag/config.py
def reload_embedder() -> None:
    global _EMBEDDER, _EMBEDDER_KEY
    _EMBEDDER = None
    _EMBEDDER_KEY = None
    get_config(reload=True)
```

`reload_embedder()`/`reload_reranker()` clear the cached instance and force
`get_config(reload=True)` to re-read `rag.yaml`, so a long-running process (an MCP
server, not a one-shot `scan`/`index`/`reindex` CLI script) can pick up a
`rag.yaml` edit — new model, new backend, new `pooling_type` — on the next
`get_embedder()`/`get_reranker()` call, without restarting the process.

---

## Chunking — `claudenv/domain/rag_chunker/chunkers.py`

One file, one function per file-type strategy, dispatched by extension in
`chunk_file()` (`claudenv/domain/rag_chunker/chunkers.py`):

| Extension | Strategy | Function |
|---|---|---|
| `.md`, `.mdx`, `.rst` | Header-hierarchy split | `_chunk_markdown` |
| `.py .js .ts .tsx .go .java .kt .cs .rs .c .h .cpp .hpp` | AST-aware (tree-sitter), falls back to window if parsing fails or no unit nodes found | `_chunk_code` |
| anything else | Sliding window | `_split_window` (called directly with `TARGETS["fallback"]`) |

Target/overlap token budgets (`TARGETS`, `claudenv/domain/rag_chunker/chunkers.py`), in
`(target_tokens, overlap_tokens)`:

| Strategy key | Target | Overlap |
|---|---|---|
| `code` | 512 | 64 |
| `markdown` | 384 | 48 |
| `section` | 256 | 32 (defined but not referenced by any current caller) |
| `fallback` | 400 | 50 |

Tokens are approximated as `len(text) // 4` (`approx_tokens`,
`claudenv/domain/rag_chunker/chunkers.py`) — there is no real tokenizer wired in.

### AST-aware code chunking

```python
# claudenv/domain/rag_chunker/chunkers.py
def get_parser(lang: str):
    if _TSParser is None or _get_language is None:
        return None
    if lang not in _PARSER_CACHE:
        try:
            _PARSER_CACHE[lang] = _TSParser(_get_language(lang))
        except Exception:
            _PARSER_CACHE[lang] = None
    return _PARSER_CACHE[lang]
```

The module deliberately builds the parser from the stable core `tree_sitter.Parser`
plus a grammar from `tree_sitter_language_pack`, rather than that pack's own
`get_parser()` — the comment at `claudenv/domain/rag_chunker/chunkers.py` explains the
pack's own parser has a divergent API (`parse()` rejects bytes, `root_node` is a
method not a property) that would break the byte-offset walk below. Unsupported or
failing languages cache as `None` and every later call for that language falls
straight to window chunking — the cache means that failure is paid once per
language, not once per file.

`_chunk_code` (`claudenv/domain/rag_chunker/chunkers.py`) walks the tree looking for
per-language "unit" node types (`UNIT_NODES`, e.g. `{"function_definition",
"class_definition"}` for Python) and does **not** descend into a node once it's
captured as a unit — nested functions/methods become part of their parent's chunk
text, not separate chunks. Two edge cases baked into `walk()`:

- A unit larger than `1.5x` the code target (768 tokens) gets sub-windowed with
  `_split_window` instead of kept whole (`claudenv/domain/rag_chunker/chunkers.py`).
- If the walk finds zero unit nodes at all (e.g. a config-ish file that happens to
  have a supported extension), the whole file falls back to window chunking
  (`claudenv/domain/rag_chunker/chunkers.py`) — same as if tree-sitter were unavailable.

`symbol_name` is extracted by scanning direct children for an
`identifier`/`name`/`type_identifier` node (`name_of`, `claudenv/domain/rag_chunker/chunkers.py`);
if none is found the node's own type string is used as the name.

### Markdown sectioning

```python
# claudenv/domain/rag_chunker/chunkers.py
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
```

Every `#`-prefixed line starts a new section named after that heading text
(truncated to 80 chars); content before the first heading is labeled `"preamble"`.
A section that grows past the 384-token markdown target is flushed mid-section too
(without waiting for the next heading), so one long section can still produce
multiple chunks — there's no overlap applied between these token-triggered splits
(the `overlap` value is unpacked but unused in this function, unlike
`_split_window`).

### Chunk identity and metadata

```python
# claudenv/domain/rag_chunker/chunkers.py
def _cid(repo: str, path: str, start: int, end: int) -> str:
    return hashlib.sha1(f"{repo}:{path}:{start}:{end}".encode()).hexdigest()

def _mk(repo, branch, commit, path, ftype, stype, name, s, e, text) -> Chunk:
    return Chunk(
        chunk_id=_cid(repo, path, s, e), repo=repo, branch=branch,
        commit_sha=commit, file_path=path, file_type=ftype, symbol_type=stype,
        symbol_name=name, start_line=s, end_line=e, text=text,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
    )
```

`chunk_id` is a SHA-1 of `repo:path:start:end` — stable across re-indexing runs as
long as line ranges don't shift, which is what makes `LanceStore.upsert`'s
delete-then-add idempotent. `content_hash` (SHA-256 of the chunk text) is separate
metadata, presumably for an indexer to skip re-embedding unchanged chunks — but
`chunkers.py` itself never reads it back; that comparison, if it happens, lives in
the indexer, not here. Empty/whitespace-only parts are dropped before becoming
`Chunk`s (`if t.strip():`, `claudenv/domain/rag_chunker/chunkers.py`).

---

## Embedding — `claudenv/adapters/embedding/embedders.py`, `llama_embedder.py`

```python
from rag.config import get_embedder
emb = get_embedder()
vecs = emb.embed_documents(["def f(): ..."])
qv   = emb.embed_query("how is jwt validated")
```

`EmbedderBackend` (`claudenv/adapters/embedding/embedders.py`) is the ABC every embedder backend
implements: `embed_documents`, `embed_query` (abstract), plus `dim`, `model_name`,
`backend_name` attributes and an `info()` method returning
`{backend, model_name, dim}` for audit logging. `LlamaEmbedder` is the one
built-in implementation (`backend_name = "llama_cpp"`).

Constructor signature (`claudenv/adapters/embedding/embedders.py`):

```python
def __init__(self, model_path: str, model_name: str, embedding_dim: int,
             n_ctx: int = 2048, n_gpu_layers: int = -1,
             n_threads: int | None = None,
             document_prefix: str = "", query_prefix: str = "",
             pooling_type: str = "mean"):
```

It wraps `llama_cpp.Llama(model_path=..., embedding=True, pooling_type=...,
n_ctx=..., n_threads=..., n_gpu_layers=..., verbose=False)`. `pooling_type` (a
string — `mean`/`cls`/`last`/`none`) is mapped to the matching
`llama_cpp.llama_cpp.LLAMA_POOLING_TYPE_*` constant; an unrecognized string raises
`ValueError` listing the valid options before the model is even loaded. If
`llama_cpp` isn't importable, `Llama` is set to `None` at import time and the
constructor raises `RuntimeError` with the exact install command
(`CMAKE_ARGS='-DLLAMA_METAL=on' pip install llama-cpp-python`) — this is a hard
failure, not a silent no-op embedder. `n_threads` defaults to `os.cpu_count() or 8`
when not given.

```python
# claudenv/adapters/embedding/llama_embedder.py
def _embed(self, text: str) -> list[float]:
    out = self.llm.create_embedding(text)
    vec = out["data"][0]["embedding"]
    if vec and isinstance(vec[0], list):
        raise TypeError(
            f"Embedding model returned {len(vec)} per-token vectors instead of one "
            f"pooled vector. Set embedding.pooling_type in rag.yaml (e.g. 'mean') "
            f"to match your model.")
    # L2 normalize for cosine == dot
    norm = sum(x * x for x in vec) ** 0.5 or 1.0
    return [x / norm for x in vec]

def embed_documents(self, texts: list[str]) -> list[list[float]]:
    return [self._embed(self._doc(t)) for t in texts]

def embed_query(self, text: str) -> list[float]:
    return self._embed(self._query(text))
```

The `isinstance(vec[0], list)` guard exists because a GGUF that doesn't bake in
pooling — combined with `pooling_type` left at its config default or set wrong for
that model — makes `llama_cpp` return one embedding *per token* instead of one
pooled vector for the whole input; without the guard, `_embed()` would silently
treat the token count as the vector length and L2-normalize the wrong shape, which
surfaces much later as a dimension mismatch against the LanceDB schema instead of
a clear error here. Every (correctly pooled) vector is L2-normalized before it's
returned (the `or 1.0` guards a theoretical all-zero vector from dividing by
zero), so LanceDB's cosine similarity and a plain dot product are interchangeable
downstream. `embed_documents` and `embed_query` differ only in which configured
prefix (`self._doc_prefix`/`self._query_prefix`) gets prepended — the model itself
is not inspected to decide whether a prefix (or a pooling type) is needed; that's
a config decision, per the module's own docstring
(`claudenv/adapters/embedding/embedders.py`). Two static helpers, `to_blob`/`from_blob`
(`claudenv/adapters/embedding/embedders.py`), pack/unpack a vector as little-endian
float32 — for storing raw vectors in a SQLite BLOB column if a caller needs that
instead of LanceDB.

---

## Reranking — `claudenv/adapters/embedding/rerankers.py`, `cross_encoder.py`

```python
from rag.config import get_reranker
rr = get_reranker()
if not rr.ok:
    print(rr.status())   # explains why reranking is inactive
```

`RerankerBackend` (`claudenv/adapters/embedding/rerankers.py`) is the ABC every reranker backend
implements: `rerank` (abstract), plus `ok`, `model_name`, `backend_name`
attributes and a `status()` method returning `{ok, backend, model_name, error}`.
`CrossEncoderReranker` is the one built-in implementation
(`backend_name = "onnx_cross_encoder"`).

Construction never raises — `CrossEncoderReranker.__init__` catches everything and
sets `self.ok = False` with `self._load_error` on any failure (missing
`model_dir`, missing `onnxruntime`/`transformers`, missing `model.onnx`, bad
tokenizer files):

```python
# claudenv/adapters/embedding/cross_encoder.py
if not model_dir:
    self._load_error = "reranker disabled (no model_dir configured)"
    return
try:
    import onnxruntime as ort
    from transformers import AutoTokenizer
    self.tok = AutoTokenizer.from_pretrained(model_dir)
    self.sess = ort.InferenceSession(
        str(Path(model_dir) / "model.onnx"),
        providers=["CPUExecutionProvider"])
    self.ok = True
except Exception as exc:
    self._load_error = str(exc)
    self.ok = False  # identity fallback
```

`rerank(query, candidates, top_n=8, text_key="text")`
(`claudenv/adapters/embedding/rerankers.py`) is the one entry point:

- Empty candidates → `[]` immediately.
- `not self.ok` → identity fallback: `candidates[:top_n]`, unchanged order — this
  is how a missing/disabled reranker degrades to "just trust fusion order" instead
  of erroring the whole query.
- Otherwise: tokenizes `(query, candidate_text)` pairs together (`padding=True,
  truncation=True, max_length=512`), runs the ONNX session with
  `providers=["CPUExecutionProvider"]` (CPU-only, no GPU dependency), sorts by
  descending logit with `np.argsort(-logits)`, and returns the top `top_n`
  candidates each with an added `rerank_score` float field.

`status()` (`claudenv/adapters/embedding/rerankers.py`) returns `{ok, model_dir,
model_name, error}` — `error` is `None` whenever `ok` is `True`, otherwise the
caught exception's string. Per the module docstring, reranking improves top-3
precision roughly 15-25% over fusion alone (a stated claim from the source
comments — not independently re-verified in this doc).

---

## Storage & hybrid search — `claudenv/adapters/vector/lancedb/vector_store.py`

### Schema

```python
# claudenv/adapters/vector/lancedb/vector_store.py
def _schema(self) -> "pa.Schema":
    return pa.schema([
        pa.field("chunk_id", pa.string()),
        pa.field("vector", pa.list_(pa.float32(), self.dim)),
        pa.field("text", pa.string()),
        pa.field("repo", pa.string()),
        pa.field("branch", pa.string()),
        pa.field("commit_sha", pa.string()),
        pa.field("file_path", pa.string()),
        pa.field("file_type", pa.string()),
        pa.field("symbol_type", pa.string()),
        pa.field("symbol_name", pa.string()),
        pa.field("start_line", pa.int32()),
        pa.field("end_line", pa.int32()),
        pa.field("content_hash", pa.string()),
        pa.field("tier", pa.int32()),
    ])
```

`self.dim` (default `768`, matching `embedding.embedding_dim`) is baked directly
into the `vector` field's fixed-size list type — changing `embedding_dim` means
every existing table's schema is now wrong, which is why the config comment calls
out that a dimension change needs a full re-index.

### One table per (repo, branch)

```python
# claudenv/adapters/vector/lancedb/vector_store.py
def table_name(repo: str, branch: str) -> str:
    safe = lambda s: s.replace("/", "-").replace(" ", "_")
    return f"{safe(repo)}__{safe(branch)}"
```

On disk this is `~/.claude-env/knowledge/lancedb/<repo-slug>__<branch>.lance/`
(default `LANCE_PATH`, overridable via `LANCEDB_PATH`). `open()`
(`claudenv/adapters/vector/lancedb/vector_store.py`) creates the table with the schema above if
it doesn't exist yet and immediately tries `tbl.create_fts_index("text",
replace=True)`, swallowing any exception — so a LanceDB build without FTS support
still works, just without hybrid search (see below).

### Upsert, delete, count

```python
# claudenv/adapters/vector/lancedb/vector_store.py
def upsert(self, repo: str, branch: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    tbl = self.open(repo, branch)
    ids = [r["chunk_id"] for r in rows]
    # delete-then-add = idempotent upsert keyed by chunk_id
    id_list = ",".join(f"'{i}'" for i in ids)
    try:
        tbl.delete(f"chunk_id IN ({id_list})")
    except Exception:
        pass
    tbl.add(rows)
    return len(rows)
```

LanceDB has no native upsert, so this is delete-by-id-list then add — idempotent
because `chunk_id` is a deterministic hash of `(repo, path, start_line,
end_line)` (see chunking section above). `delete_file()`
(`claudenv/adapters/vector/lancedb/vector_store.py`) removes all rows for a `file_path`,
escaping embedded single quotes (`replace("'", "''")`) so a real path like
`docs/what's-new.md` doesn't break the generated SQL filter mid-run. `count()`
(`claudenv/adapters/vector/lancedb/vector_store.py`) returns `0` on any failure rather than
raising.

### Hybrid search

```python
# claudenv/adapters/vector/lancedb/vector_store.py
def search(self, repo: str, branch: str, query_vector: list[float],
           query_text: str, top_k: int = 40) -> list[dict]:
    tbl = self.open(repo, branch)
    try:
        res = (tbl.search(query_type="hybrid")
                  .vector(query_vector).text(query_text)
                  .limit(top_k).to_list())
    except Exception:
        res = tbl.search(query_vector).limit(top_k).to_list()
    for r in res:
        r.pop("vector", None)
    return res
```

The primary path asks LanceDB itself for `query_type="hybrid"` — dense cosine
vector search fused with BM25-style full-text search via reciprocal-rank fusion,
per the module docstring (`claudenv/adapters/vector/lancedb/vector_store.py`). If that raises
for any reason (most commonly: no FTS index exists because `create_fts_index`
failed silently in `open()`), the `except` falls back to plain vector-only search
with the same `top_k`. Either way, the raw `vector` field is stripped from every
result row before returning — callers get metadata + score fields, never the
1536-plus raw floats back. Downstream, `claudenv/application/rag/service.py` is what
interprets whichever score fields a given path produced (`rerank_score`,
`_relevance_score` from the hybrid path, or `_distance` from the vector-only
path) — see [`retrieval-poison-screening.md`](retrieval-poison-screening.md) and
its regression coverage in `tests/test_retrieve_ranking.py`, which exists
specifically because the hybrid path's `_relevance_score` field was once dropped
by an incomplete sort key. **No dedicated test file exists for the chunkers,
embedder, reranker, or `LanceStore` themselves** — this doc's code excerpts are
the closest thing to verified behavior beyond the source itself; don't assume
coverage that isn't there.

![Indexing pipeline](../assets/guide/rag-pipeline/indexing-pipeline.svg)

---

## Flow diagrams

![RAG indexing pipeline: file to chunker to embedder to LanceDB](../assets/guide/rag-pipeline/indexing-pipeline.svg)
*A file is chunked by extension-driven strategy, each chunk is embedded and
L2-normalized, and the vector + metadata is upserted into a per-(repo, branch)
LanceDB table that also maintains a full-text index.*

![Model path resolution order: env var, then yaml, then fallback](../assets/guide/rag-pipeline/model-resolution-order.svg)
*Every embedding/reranker setting resolves env var → `rag.yaml` → built-in
fallback, except the model path itself, which has no fallback and fails loud via
`RuntimeError` when unconfigured.*

---

## Facts, invariants & edge cases

- **No model name or path is ever hardcoded** (see `claudenv/domain/rag/config.py` above) — every
  constructor only accepts paths/dirs from config, and grepping the
  embedder/reranker source for a literal model filename turns up none.
- **An unconfigured embedding model fails loud, not silently** — `get_embedder()`
  raises `RuntimeError` with setup instructions when `embedding.model_path` is
  empty (shown above); no default/example model is shipped or assumed.
- **An unconfigured or broken reranker degrades gracefully instead** — unlike the
  embedder, `CrossEncoderReranker` never raises on construction; `ok=False` plus
  identity-order passthrough in `rerank()` is the designed fallback (see above),
  since reranking is explicitly optional.
- **Both the embedder and reranker instances are cached** at module level in
  `claudenv/domain/rag/config.py`, keyed by `(backend, model_path)` / `(backend, model_dir)` (see
  above) — a `rag.yaml` edit alone doesn't take effect on an already-running
  process; call `reload_embedder()`/`reload_reranker()` or restart it.
- **Adding a new embedder/reranker backend never touches `claudenv/domain/rag/config.py`'s
  factories** — implement `EmbedderBackend`/`RerankerBackend` (`base.py`) and
  register the class in the matching `registry.py`'s `BACKENDS` dict (see
  ["Swapping backends, not just models"](#swapping-backends-not-just-models)
  above). An unregistered `backend` string in `rag.yaml` raises `ValueError`
  listing the valid keys, rather than silently falling back to something else.
- **A wrong `embedding.pooling_type` produces a dimension/type mismatch, not a
  clean pooling error** — `_embed()` (`claudenv/adapters/embedding/embedders.py`) detects
  unpooled per-token output and raises `TypeError` naming `pooling_type`
  specifically, rather than letting the wrong shape propagate into the LanceDB
  upsert as an unrelated-looking error (see above). `LlamaEmbedder.__init__` also
  runs a one-time `_self_check()` — a real embed call against a throwaway string —
  so a misconfigured `pooling_type`/`embedding_dim` fails at model-construction
  time, not partway through embedding a large repo.
- **A model can emit NaN for a given input** — observed indexing real repos with
  Qwen3-VL-Embedding-8B against small config/template files. `_embed()`'s
  L2-normalize step (`x / norm`) propagates a single NaN into every component of
  that vector; LanceDB's Arrow layer rejects a NaN vector with an opaque error
  naming no file or chunk, and (before this was fixed) aborted the entire
  upsert batch for that file. `Indexer._index_one()`
  (`claudenv/application/rag/indexer.py`) now filters non-finite (NaN/Inf) vectors per-chunk
  before building rows, audit-logs which file was affected via
  `security_event("indexing", ...)`, and keeps indexing the rest of the file/repo
  — a chunk with a non-finite vector is simply not indexed, not a crash.
- **`Indexer._validate_embedder_dim(branch)` is read-only** — it must never create
  a table. An earlier version called `LanceStore.open()` (which auto-creates a
  table matching the *current* embedder's schema if missing) using a hardcoded
  `"master"` branch regardless of the repo's actual branch; this both left behind
  a spurious empty table for a branch that was never indexed, and made the
  dimension check a structural no-op (a raised `ValueError` was even swallowed by
  the method's own broad `except Exception`, so it never stopped indexing on a
  real mismatch). The fixed version checks `table_names()` before opening, is
  keyed by the repo's real current branch (computed before `_lazy()` is called in
  `full_index()`/`incremental()`), and a genuine dimension conflict now
  propagates and halts indexing.
- **Vectors are always L2-normalized before storage or query**, so LanceDB's
  cosine metric and a raw dot product agree (`claudenv/adapters/embedding/embedders.py`,
  see above).
- **Changing `embedding_dim` requires a full re-index** — the dimension is fixed
  into the LanceDB `vector` field type at table-creation time (see above); an
  existing table isn't migrated.
- **`chunk_id` is a hash of `(repo, path, start_line, end_line)`, not of the text
  itself** (see above) — if a file's content changes but chunk boundaries don't
  shift, the old chunk_id is reused and the row is overwritten; `content_hash`
  is stored alongside for an indexer to notice the change, but
  `chunkers.py`/`lance_store.py` don't themselves compare it to skip work.
- **A large AST unit is sub-windowed, not truncated** (see above) — anything over
  1.5x the 512-token code target (768 tokens) is split with the same
  sliding-window logic as the fallback path, rather than embedded whole or cut
  off.
- **tree-sitter parser failures are cached as `None` per language** (see above),
  so a broken or unsupported grammar only pays the failed-import/build cost
  once, not per file.
- **The pack's own `get_parser()` is deliberately not used**, because its API is
  incompatible with this module's byte-offset walk (see above) — a maintenance
  trap if "simplified" later without reading that comment.
- **Nested functions don't get their own chunk** — `walk()` returns immediately
  after capturing a unit node without descending into its children (see above),
  so an inner function is embedded only as part of its enclosing chunk.
- **Markdown section overlap is defined but unused** — `TARGETS["markdown"]`
  unpacks an `overlap` value in `_chunk_markdown`, but nothing in that function
  re-includes trailing lines across a token-triggered mid-section flush (see
  above); only heading boundaries get a clean section start.
- **LanceDB has no native upsert; this module fakes one** via delete-by-id-list
  then `add()` (see above) — a crash between the two would leave rows missing
  until the next successful run; there is no transaction wrapping them.
- **Hybrid search silently degrades to vector-only** on any exception from the
  `query_type="hybrid"` path, most commonly a missing FTS index (see above) —
  callers can't distinguish "hybrid ran" from "fell back to vector-only" from
  the return value alone.
- **File paths with single quotes are escaped for `delete_file()`** (see above)
  — without it, a path like `docs/what's-new.md` would break the generated
  filter and crash indexing mid-run.
- **No test file exists for chunkers or `LanceStore`.** `tests/test_retrieve_ranking.py`
  covers `rag/pipelines/retrieve.py::_relevance` (the score-selection helper used
  after `LanceStore.search()` returns); `tests/test_rag_model_backends.py` covers
  the embedder/reranker registry dispatch, the interface contract, the
  `(backend, path)` cache key, `reload_embedder()`/`reload_reranker()`, the
  pooling-mismatch guard, and the construction-time self-check in
  `LlamaEmbedder` (via a stub `Llama`, not a real GGUF);
  `tests/test_indexer_dim_validation.py` covers `_validate_embedder_dim()`'s
  read-only/real-branch/mismatch-propagates behavior; `tests/test_indexer_nan_vectors.py`
  covers the per-chunk NaN/Inf skip in `_index_one()`. Chunkers and `LanceStore`
  itself remain uncovered.

---

## Related docs

- [`retrieval-poison-screening.md`](retrieval-poison-screening.md) — how a query
  actually flows through `claudenv/application/rag/service.py`: fusion/rerank orchestration,
  and how retrieved text is screened as untrusted data before reaching an agent.
- [OVERVIEW.md §5](../OVERVIEW.md#5-knowledge-and-search-shouldnt-leave-the-building) —
  the product-level framing: local-only knowledge and search.
- `mcp-servers.md` *(planned)* — the `lancedb-rag` MCP server that exposes this
  pipeline's search to agents, alongside the other five servers.
- [`policy-engine.md`](policy-engine.md) — `rag.index_paths` is a subset of what
  the policy engine already allows to be read; deny always wins over index scope.
