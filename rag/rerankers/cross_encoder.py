"""
claude-env :: reranker
File: rag/rerankers/cross_encoder.py
Purpose:
    Re-rank hybrid-search candidates with a local cross-encoder ONNX model.
    Improves top-3 precision ~15-25% over fusion alone.

    If onnxruntime / the model directory is unavailable (or reranking is disabled),
    falls back to identity (keeps the fusion order) so the pipeline still works.

This module knows HOW to run an ONNX cross-encoder. It does NOT decide WHICH model
to use, nor does it know any model's name — the directory is configured manually at
setup in config/rag.yaml (`reranker.model_dir`) or via the RERANKER_DIR env var.
Construct via the factory:

    from rag.config import get_reranker
    rr = get_reranker()
    if not rr.ok:
        print(rr.status())   # explains why reranking is inactive

Point the configured directory at any cross-encoder exported to ONNX; it must
contain `model.onnx` plus the tokenizer files. See README §4 "Install local models"
for export instructions and suggested models.

Set RERANKER_DIR="" (or reranker.model_dir: "" in rag.yaml) to disable reranking.
"""
from __future__ import annotations

from pathlib import Path

from rag.rerankers.base import RerankerBackend


class CrossEncoderReranker(RerankerBackend):
    backend_name = "onnx_cross_encoder"

    def __init__(self, model_dir: str = ""):
        self.model_dir = model_dir
        self.model_name = Path(model_dir).name if model_dir else ""
        self.ok = False
        self._load_error: str = ""

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

    @classmethod
    def from_config(cls, cfg) -> "CrossEncoderReranker":
        """Build from a rag.config.RerankerConfig (the only sanctioned path)."""
        return cls(model_dir=cfg.model_dir)

    def status(self) -> dict:
        """Return a dict describing whether the reranker loaded successfully."""
        return {
            "ok": self.ok,
            "model_dir": self.model_dir,
            "model_name": self.model_name,
            "error": self._load_error if not self.ok else None,
        }

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
