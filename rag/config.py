"""
claude-env :: RAG model configuration loader
File: rag/config.py

Single source of truth for which embedding + reranker models the RAG stack uses.
Resolution order (highest priority first):

    1. Environment variables  (EMBED_MODEL_PATH, RERANKER_DIR, ...)
    2. config/rag.yaml          (the file next to this repo's config/ dir)
    3. Built-in fallbacks       (sizes/dims only — NEVER a model name or path)

No model name, filename, or path is hardcoded anywhere in the code. The embedding
model is chosen MANUALLY AT SETUP by setting `embedding.model_path` in
config/rag.yaml (or the EMBED_MODEL_PATH env var). If no model is configured,
get_embedder() raises a clear error instead of guessing.

Nothing in the RAG code constructs an embedder or reranker directly — they call
the factories here (get_embedder / get_reranker). That keeps the choice of model
in *one* place (config, not code) and lets you swap models with a yaml edit or an
env var, no code change required.

No symlinks are used anywhere: the configured path points straight at the model
file/directory wherever it lives.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Built-in fallbacks — the lowest-priority layer. These are model-AGNOSTIC: only
# numeric/string knobs that have a sane neutral default. The model path is
# intentionally absent (empty) so it must be configured at setup; an unconfigured
# model fails loud rather than silently pointing at some assumed file.
_BUILTIN = {
    "embedding": {
        "model_path": "",          # REQUIRED — set in config/rag.yaml at setup
        "n_ctx": 2048,
        "n_gpu_layers": -1,
        "embedding_dim": 768,
        "document_prefix": "",     # optional task prefix; set in config/rag.yaml
        "query_prefix": "",        # optional task prefix; set in config/rag.yaml
    },
    "reranker": {
        "model_dir": "",           # optional — empty disables reranking
    },
}


def _resolve_yaml_path() -> Path:
    """Locate config/rag.yaml, checking (in order):
    1. RAG_CONFIG_YAML env var (explicit override)
    2. <root>/config/rag.yaml relative to this file (works in both the repo and
       the deployed $CLAUDE_ENV_HOME tree, since rag/config.py is one level down)
    3. $CLAUDE_ENV_HOME/config/rag.yaml
    Returns the first that exists, else option 2 (so the path is still well-defined
    and the loader falls back to built-in defaults).
    """
    override = os.environ.get("RAG_CONFIG_YAML")
    if override:
        return Path(os.path.expanduser(override))
    relative = Path(__file__).resolve().parents[1] / "config" / "rag.yaml"
    if relative.exists():
        return relative
    home = Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))
    home_yaml = home / "config" / "rag.yaml"
    if home_yaml.exists():
        return home_yaml
    return relative


_DEFAULT_YAML = _resolve_yaml_path()


def _expand(p: str) -> str:
    """Expand ~ and environment variables in a path string."""
    return os.path.expanduser(os.path.expandvars(p)) if p else p


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into a copy of base (override wins)."""
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        elif v is not None:
            out[k] = v
    return out


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    try:
        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        return {}


@dataclass
class EmbeddingConfig:
    model_path: str
    model_name: str
    n_ctx: int
    n_gpu_layers: int
    embedding_dim: int
    document_prefix: str = ""   # prepended to documents before embedding
    query_prefix: str = ""      # prepended to queries before embedding


@dataclass
class RerankerConfig:
    model_dir: str   # "" disables reranking


@dataclass
class RagConfig:
    embedding: EmbeddingConfig
    reranker: RerankerConfig

    @classmethod
    def load(cls, yaml_path: Path | None = None) -> "RagConfig":
        merged = _deep_merge(_BUILTIN, _load_yaml(yaml_path or _DEFAULT_YAML))
        emb = merged["embedding"]
        rer = merged["reranker"]

        # --- env overrides (highest priority) ---
        model_path = _expand(os.environ.get("EMBED_MODEL_PATH", emb["model_path"]))
        # The label written to rag_index_state. Explicit EMBED_MODEL_NAME wins;
        # otherwise derive from the resolved path so it tracks the model. Empty
        # path -> empty name (the empty-path case is caught by get_embedder()).
        model_name = os.environ.get("EMBED_MODEL_NAME") \
            or (Path(model_path).stem if model_path else "")
        n_ctx = int(os.environ.get("EMBED_CTX", emb["n_ctx"]))
        n_gpu_layers = int(os.environ.get("EMBED_GPU_LAYERS", emb["n_gpu_layers"]))
        embedding_dim = int(os.environ.get("EMBED_DIM", emb["embedding_dim"]))
        document_prefix = os.environ.get("EMBED_DOC_PREFIX", emb.get("document_prefix", ""))
        query_prefix = os.environ.get("EMBED_QUERY_PREFIX", emb.get("query_prefix", ""))

        # RERANKER_DIR explicitly set to "" disables reranking.
        rer_dir = os.environ.get("RERANKER_DIR")
        if rer_dir is None:
            rer_dir = rer.get("model_dir", "")
        rer_dir = _expand(rer_dir) if rer_dir else ""

        return cls(
            embedding=EmbeddingConfig(
                model_path=model_path,
                model_name=model_name,
                n_ctx=n_ctx,
                n_gpu_layers=n_gpu_layers,
                embedding_dim=embedding_dim,
                document_prefix=document_prefix,
                query_prefix=query_prefix,
            ),
            reranker=RerankerConfig(model_dir=rer_dir),
        )


# Module-level cached singleton so repeated factory calls don't re-read yaml.
_CONFIG: RagConfig | None = None


def get_config(reload: bool = False) -> RagConfig:
    global _CONFIG
    if _CONFIG is None or reload:
        _CONFIG = RagConfig.load()
    return _CONFIG


_EMBEDDER = None          # cached instance (loading the GGUF model is expensive)
_EMBEDDER_KEY = None       # model_path the cached instance was built for


def get_embedder(cfg: RagConfig | None = None):
    """Factory: return the configured embedder. Decoupled from model identity.

    The built instance is cached per model_path, so repeated callers (indexer,
    retriever, memory writes) reuse one loaded model instead of reloading the
    GGUF each call. Raises a clear error if no embedding model is configured.
    """
    global _EMBEDDER, _EMBEDDER_KEY
    from rag.embeddings.llama_embedder import LlamaEmbedder
    cfg = cfg or get_config()
    if not cfg.embedding.model_path:
        raise RuntimeError(
            "No embedding model configured. Set `embedding.model_path` in "
            "config/rag.yaml (or the EMBED_MODEL_PATH env var) to point at a local "
            "model file. See README §5 'Install local models' for download "
            "instructions and suggested models.")
    if _EMBEDDER is None or _EMBEDDER_KEY != cfg.embedding.model_path:
        _EMBEDDER = LlamaEmbedder.from_config(cfg.embedding)
        _EMBEDDER_KEY = cfg.embedding.model_path
    return _EMBEDDER


def get_reranker(cfg: RagConfig | None = None):
    """Factory: build the configured reranker. Returns an identity reranker if disabled."""
    from rag.rerankers.cross_encoder import CrossEncoderReranker
    cfg = cfg or get_config()
    return CrossEncoderReranker.from_config(cfg.reranker)
