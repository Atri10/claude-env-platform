"""Coverage for the swappable embedder/reranker backend registry (rag/config.py,
rag/embeddings/registry.py, rag/rerankers/registry.py). Run: pytest tests/ -q

Regression context: switching to a different embedding model (different backend or
just a different dimension) previously required editing rag/config.py directly and
could silently produce dimension/type mismatches (e.g. an unpooled GGUF returning
one vector per token instead of one per input). These tests cover the registry
dispatch, the interface contract, and the cache-key/reload behavior that lets a
model be swapped via config alone.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import rag.config as rag_config
from rag.config import EmbeddingConfig, RagConfig, RerankerConfig
from rag.embeddings.base import EmbedderBackend
from rag.embeddings.llama_embedder import LlamaEmbedder
from rag.embeddings.registry import BACKENDS as EMBEDDER_BACKENDS
from rag.embeddings.registry import get_backend_class as get_embedder_backend_class
from rag.rerankers.base import RerankerBackend
from rag.rerankers.cross_encoder import CrossEncoderReranker
from rag.rerankers.registry import BACKENDS as RERANKER_BACKENDS
from rag.rerankers.registry import get_backend_class as get_reranker_backend_class


class FakeEmbedder(EmbedderBackend):
    """A minimal second backend used only to prove registry dispatch works."""
    backend_name = "fake"

    def __init__(self, model_path: str, model_name: str, embedding_dim: int, **_):
        self.dim = embedding_dim
        self.model_name = model_name or "fake-model"

    @classmethod
    def from_config(cls, cfg):
        return cls(model_path=cfg.model_path, model_name=cfg.model_name,
                    embedding_dim=cfg.embedding_dim)

    def embed_documents(self, texts):
        return [[0.0] * self.dim for _ in texts]

    def embed_query(self, text):
        return [0.0] * self.dim


class FakeReranker(RerankerBackend):
    backend_name = "fake"

    def __init__(self, model_dir: str = ""):
        self.model_dir = model_dir
        self.model_name = "fake-reranker"
        self.ok = True
        self._load_error = ""

    @classmethod
    def from_config(cls, cfg):
        return cls(model_dir=cfg.model_dir)

    def rerank(self, query, candidates, top_n=8, text_key="text"):
        return candidates[:top_n]


# --- interface contract -----------------------------------------------------

def test_llama_embedder_implements_embedder_backend():
    assert issubclass(LlamaEmbedder, EmbedderBackend)
    assert LlamaEmbedder.backend_name == "llama_cpp"


def test_cross_encoder_implements_reranker_backend():
    assert issubclass(CrossEncoderReranker, RerankerBackend)
    assert CrossEncoderReranker.backend_name == "onnx_cross_encoder"


def test_embedder_backend_cannot_be_instantiated_directly():
    import pytest
    with pytest.raises(TypeError):
        EmbedderBackend()


def test_reranker_backend_cannot_be_instantiated_directly():
    import pytest
    with pytest.raises(TypeError):
        RerankerBackend()


# --- registry dispatch -------------------------------------------------------

def test_embedder_registry_has_llama_cpp_builtin():
    assert EMBEDDER_BACKENDS["llama_cpp"] is LlamaEmbedder


def test_reranker_registry_has_onnx_cross_encoder_builtin():
    assert RERANKER_BACKENDS["onnx_cross_encoder"] is CrossEncoderReranker


def test_unknown_embedder_backend_raises_clear_error():
    import pytest
    with pytest.raises(ValueError, match="Unknown embedding.backend"):
        get_embedder_backend_class("nonexistent")


def test_unknown_reranker_backend_raises_clear_error():
    import pytest
    with pytest.raises(ValueError, match="Unknown reranker.backend"):
        get_reranker_backend_class("nonexistent")


def test_registry_swap_selects_different_backend_class(monkeypatch):
    """The core 'plug and play' guarantee: adding a backend key to the registry
    is enough for get_backend_class() to dispatch to it — no factory changes."""
    monkeypatch.setitem(EMBEDDER_BACKENDS, "fake", FakeEmbedder)
    assert get_embedder_backend_class("fake") is FakeEmbedder

    monkeypatch.setitem(RERANKER_BACKENDS, "fake", FakeReranker)
    assert get_reranker_backend_class("fake") is FakeReranker


# --- factory (get_embedder/get_reranker) dispatch + caching -----------------

def _reset_factory_caches(monkeypatch):
    monkeypatch.setattr(rag_config, "_EMBEDDER", None)
    monkeypatch.setattr(rag_config, "_EMBEDDER_KEY", None)
    monkeypatch.setattr(rag_config, "_RERANKER", None)
    monkeypatch.setattr(rag_config, "_RERANKER_KEY", None)
    monkeypatch.setattr(rag_config, "_audit_model_load", lambda *a, **k: None)


def test_get_embedder_dispatches_via_configured_backend(monkeypatch):
    monkeypatch.setitem(EMBEDDER_BACKENDS, "fake", FakeEmbedder)
    _reset_factory_caches(monkeypatch)
    cfg = RagConfig(
        embedding=EmbeddingConfig(backend="fake", model_path="/fake/model.bin",
                                   model_name="fake-model", n_ctx=2048,
                                   n_gpu_layers=-1, embedding_dim=123),
        reranker=RerankerConfig(model_dir=""),
    )
    emb = rag_config.get_embedder(cfg)
    assert isinstance(emb, FakeEmbedder)
    assert emb.dim == 123


def test_get_embedder_raises_if_model_path_unset(monkeypatch):
    import pytest
    _reset_factory_caches(monkeypatch)
    cfg = RagConfig(
        embedding=EmbeddingConfig(backend="llama_cpp", model_path="", model_name="",
                                   n_ctx=2048, n_gpu_layers=-1, embedding_dim=768),
        reranker=RerankerConfig(model_dir=""),
    )
    with pytest.raises(RuntimeError, match="No embedding model configured"):
        rag_config.get_embedder(cfg)


def test_get_embedder_cache_key_includes_backend_not_just_path(monkeypatch):
    """Regression: the old cache key was model_path alone. If two configs share a
    path but differ in backend, the cache must not silently reuse the wrong
    instance."""
    monkeypatch.setitem(EMBEDDER_BACKENDS, "fake", FakeEmbedder)
    _reset_factory_caches(monkeypatch)
    same_path = "/fake/model.bin"
    cfg_fake = RagConfig(
        embedding=EmbeddingConfig(backend="fake", model_path=same_path,
                                   model_name="m", n_ctx=2048, n_gpu_layers=-1,
                                   embedding_dim=123),
        reranker=RerankerConfig(model_dir=""),
    )
    cfg_llama = RagConfig(
        embedding=EmbeddingConfig(backend="llama_cpp", model_path=same_path,
                                   model_name="m", n_ctx=2048, n_gpu_layers=-1,
                                   embedding_dim=768),
        reranker=RerankerConfig(model_dir=""),
    )
    emb1 = rag_config.get_embedder(cfg_fake)
    assert isinstance(emb1, FakeEmbedder)
    assert rag_config._EMBEDDER_KEY == ("fake", same_path)

    # Switching backend with the same path must attempt a rebuild via the NEW
    # backend, not silently reuse the cached FakeEmbedder because the path matched.
    # llama_cpp fails to load a fake path, proving get_embedder() didn't take the
    # cache-hit shortcut (a cache hit would have returned emb1 with no error).
    import pytest
    with pytest.raises(Exception):
        rag_config.get_embedder(cfg_llama)


def test_reload_embedder_clears_cache(monkeypatch):
    monkeypatch.setitem(EMBEDDER_BACKENDS, "fake", FakeEmbedder)
    _reset_factory_caches(monkeypatch)
    cfg = RagConfig(
        embedding=EmbeddingConfig(backend="fake", model_path="/fake/model.bin",
                                   model_name="m", n_ctx=2048, n_gpu_layers=-1,
                                   embedding_dim=123),
        reranker=RerankerConfig(model_dir=""),
    )
    emb1 = rag_config.get_embedder(cfg)
    rag_config.reload_embedder()
    assert rag_config._EMBEDDER is None
    assert rag_config._EMBEDDER_KEY is None
    emb2 = rag_config.get_embedder(cfg)
    assert emb1 is not emb2


def test_get_reranker_dispatches_via_configured_backend(monkeypatch):
    monkeypatch.setitem(RERANKER_BACKENDS, "fake", FakeReranker)
    _reset_factory_caches(monkeypatch)
    cfg = RagConfig(
        embedding=EmbeddingConfig(backend="llama_cpp", model_path="", model_name="",
                                   n_ctx=2048, n_gpu_layers=-1, embedding_dim=768),
        reranker=RerankerConfig(model_dir="/fake/dir", backend="fake"),
    )
    rr = rag_config.get_reranker(cfg)
    assert isinstance(rr, FakeReranker)
    assert rr.ok


# --- pooling_type config plumbing -------------------------------------------

def test_embedding_config_pooling_type_defaults_to_mean():
    cfg = EmbeddingConfig(backend="llama_cpp", model_path="/x.gguf", model_name="x",
                           n_ctx=2048, n_gpu_layers=-1, embedding_dim=768)
    assert cfg.pooling_type == "mean"


def test_rag_config_load_reads_pooling_type_from_yaml(tmp_path, monkeypatch):
    yaml_path = tmp_path / "rag.yaml"
    yaml_path.write_text(
        "embedding:\n"
        "  model_path: /x.gguf\n"
        "  embedding_dim: 4096\n"
        "  pooling_type: cls\n"
    )
    monkeypatch.delenv("EMBED_POOLING_TYPE", raising=False)
    cfg = RagConfig.load(yaml_path)
    assert cfg.embedding.pooling_type == "cls"
    assert cfg.embedding.embedding_dim == 4096


# --- pooling mismatch guard (the original Qwen3 bug) ------------------------
# Regression for: a GGUF without pooling baked in returns one embedding PER
# TOKEN when no pooling_type is forced, instead of one pooled vector per input.
# _embed() must fail loudly with an actionable message instead of silently
# L2-normalizing the wrong shape.

class _StubLlama:
    """Stands in for llama_cpp.Llama so _embed()'s post-processing can be
    tested without a real GGUF file or the llama-cpp-python binary."""
    def __init__(self, embedding_output):
        self._embedding_output = embedding_output

    def create_embedding(self, text):
        return {"data": [{"embedding": self._embedding_output}]}


def _make_embedder_with_stub_llm(embedding_output):
    emb = LlamaEmbedder.__new__(LlamaEmbedder)  # bypass __init__: no real model load
    emb.dim = 4096
    emb.model_name = "stub"
    emb._doc_prefix = ""
    emb._query_prefix = ""
    emb.llm = _StubLlama(embedding_output)
    return emb


def test_embed_raises_on_unpooled_per_token_output():
    import pytest
    unpooled = [[0.1, 0.2, 0.3] for _ in range(5)]  # 5 tokens, not one pooled vector
    emb = _make_embedder_with_stub_llm(unpooled)
    with pytest.raises(TypeError, match="pooling_type"):
        emb._embed("hello world")


def test_embed_succeeds_on_properly_pooled_output():
    pooled = [0.1] * 4096
    emb = _make_embedder_with_stub_llm(pooled)
    vec = emb._embed("hello world")
    assert len(vec) == 4096
    # L2-normalized
    norm = sum(x * x for x in vec) ** 0.5
    assert abs(norm - 1.0) < 1e-6
