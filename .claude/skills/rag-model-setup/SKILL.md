---
name: rag-model-setup
description: >-
  Configure or swap the RAG embedding/reranker model in config/rag.yaml —
  probing a GGUF's real output dimension and required pooling type, picking
  the right backend, and validating end-to-end before a full re-index. Load
  this when adding a new embedding/reranker model, switching model family, or
  debugging a dimension/type mismatch during indexing.
---

# RAG model setup

`rag/config.py` is the single place model choice is configured (see its docstring and
`config/rag.yaml`). This skill is the checklist for changing that configuration safely —
whether you're setting it up for the first time or swapping to a different model.

## Why this needs a checklist, not just an edit

Embedding models vary in three ways that aren't visible from the file name alone:
1. **Output dimension** — varies by model family/size, must match `embedding_dim` exactly.
2. **Pooling** — a GGUF's raw output can be per-token embeddings (one vector per token) instead
   of one pooled vector per input, depending on how it was converted and what
   `embedding.pooling_type` is set to. Getting this wrong produces a dimension/type mismatch
   during indexing that looks like a bug, not a config error.
3. **Task prefixes** — some models (e.g. nomic-embed-text) require a literal prefix string
   prepended to documents vs. queries; wrong/missing prefixes silently degrade retrieval
   quality rather than erroring.

Guessing any of these wastes a full re-index cycle. Probe first.

## Steps

1. **Identify the backend.** Check `rag/embeddings/registry.py` / `rag/rerankers/registry.py`
   for the available `backend` keys. If your model needs a backend that isn't listed (e.g. an
   ONNX embedder, not llama.cpp), that's a code change (implement `EmbedderBackend`/
   `RerankerBackend`, register it) — stop and scope that separately; don't hack it into the
   existing backend class.

2. **Probe the real embedding dimension and pooling requirement** before touching `rag.yaml`.
   Do NOT trust the model card's advertised dimension blindly — verify against the actual GGUF:

   ```python
   from llama_cpp import Llama
   llm = Llama(model_path="<path-to-gguf>", embedding=True, n_ctx=2048,
               n_gpu_layers=-1, verbose=False, pooling_type=1)  # 1 = LLAMA_POOLING_TYPE_MEAN
   out = llm.create_embedding("hello world")
   vec = out["data"][0]["embedding"]
   assert isinstance(vec[0], float), "still per-token — try pooling_type=2 (CLS) or 3 (LAST)"
   print("real dim:", len(vec))
   ```

   If forcing `pooling_type=1` (mean) doesn't give a flat `list[float]`, try `2` (CLS) or `3`
   (LAST) — whichever gives a flat vector is the pooling type to put in `rag.yaml`.

3. **Check for an existing index dimension conflict.** If `~/.claude-env/knowledge/lancedb` (or
   wherever `rag.yaml`'s store points) already has a table, its vector column is a fixed
   dimension — a new model with a different dimension WILL NOT fit into that table.

   ```python
   import lancedb
   db = lancedb.connect("<path-to-lancedb>")
   for name in db.table_names():
       tbl = db.open_table(name)
       for f in tbl.schema:
           if f.name == "vector":
               print(name, f.type)  # fixed_size_list<item: float>[N]
   ```

   If the dimension differs from your new model's probed dimension, the existing index is
   incompatible — back it up (`mv ... ...bak-<old-dim>dim`) rather than deleting; a full
   re-index is required either way.

4. **Update `config/rag.yaml`** (the deployed copy at `$CLAUDE_ENV_HOME/config/rag.yaml` — see
   the claude-env-development skill's deploy model) with the probed values:
   ```yaml
   embedding:
     backend: "llama_cpp"          # or whatever registry key applies
     model_path: "~/.claude-env/models/embedding-models/<file>.gguf"
     embedding_dim: <probed dim>
     pooling_type: "mean"          # or cls/last, whichever probing found
     document_prefix: ""           # only if the model card says so
     query_prefix: ""
   ```

5. **Smoke-test through the real factory**, not just the raw model, to confirm the whole
   pipeline agrees on dimension:
   ```python
   from rag.config import get_embedder
   emb = get_embedder()
   vecs = emb.embed_documents(["a test string"])
   assert len(vecs[0]) == emb.dim
   print(emb.info())
   ```

6. **Re-index.** Delete/rename the old-dimension LanceDB table if one exists, then run the
   repo's full-index entrypoint (`Indexer.full_index()` / `bootstrap_rag.py`).

7. **If this is a long-running process** (an MCP server, not a one-shot CLI script) picking up
   a model change without restart, call `rag.config.reload_embedder()` /
   `reload_reranker()` after editing `rag.yaml`, instead of restarting the process.

## Don't

- Don't hardcode a model name, pooling type, or dimension into `rag/embeddings/llama_embedder.py`
  or any backend class — those stay in `config/rag.yaml` per this repo's invariant ("Model
  choice lives in config, never in code" in `CLAUDE.md`).
- Don't skip step 3 — reusing an old-dimension LanceDB table with a new-dimension model fails at
  upsert time with a much less clear error than catching it up front.
- Don't guess pooling type from the model name; probe it (step 2). Different quantizations/
  conversions of the "same" model can differ in whether pooling is baked in.
