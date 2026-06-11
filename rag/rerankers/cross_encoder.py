"""
claude-env :: reranker
File: rag/rerankers/cross_encoder.py
Purpose:
    Re-rank hybrid-search candidates with a local cross-encoder. Default model
    is ms-marco-MiniLM-L-6-v2 exported to ONNX (~22MB), running on CPU/ANE via
    onnxruntime. Improves top-3 precision ~15-25% over fusion alone.

    If onnxruntime / the model is unavailable, falls back to identity (keeps
    the fusion order) so the pipeline still works during bootstrap.

Model prep (documented in docs/RUNBOOK.md):
    optimum-cli export onnx --model cross-encoder/ms-marco-MiniLM-L-6-v2 \
        ~/.claude-env/models/reranker-onnx/
"""
from __future__ import annotations

import os
from pathlib import Path

RERANKER_DIR = os.environ.get(
    "RERANKER_DIR", str(Path.home() / ".claude-env/models/reranker-onnx"))


class CrossEncoderReranker:
    def __init__(self, model_dir: str = RERANKER_DIR):
        self.ok = False
        try:
            import onnxruntime as ort
            from transformers import AutoTokenizer
            self.tok = AutoTokenizer.from_pretrained(model_dir)
            self.sess = ort.InferenceSession(
                str(Path(model_dir) / "model.onnx"),
                providers=["CPUExecutionProvider"])
            self.ok = True
        except Exception:
            self.ok = False  # identity fallback

    def rerank(self, query: str, candidates: list[dict], top_n: int = 8,
               text_key: str = "text") -> list[dict]:
        if not candidates:
            return []
        if not self.ok:
            return candidates[:top_n]
        import numpy as np
        pairs = [[query, c[text_key]] for c in candidates]
        enc = self.tok([p[0] for p in pairs], [p[1] for p in pairs],
                       padding=True, truncation=True, max_length=512,
                       return_tensors="np")
        feeds = {k: v for k, v in enc.items()
                 if k in {i.name for i in self.sess.get_inputs()}}
        logits = self.sess.run(None, feeds)[0].reshape(-1)
        order = np.argsort(-logits)
        out = []
        for i in order[:top_n]:
            c = dict(candidates[i])
            c["rerank_score"] = float(logits[i])
            out.append(c)
        return out
