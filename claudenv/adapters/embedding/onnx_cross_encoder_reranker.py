"""
claude-env :: Adapters - Reranking - ONNX cross-encoder backend
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .reranker_backend import RerankerBackend


class OnnxCrossEncoderReranker(RerankerBackend):
    """ONNX Runtime cross-encoder reranker."""

    def __init__(self, model_dir: str):
        try:
            import onnxruntime as ort
            import numpy as np
        except ImportError:
            raise RuntimeError("onnxruntime not installed")

        model_path = Path(model_dir) / "model.onnx"
        if not model_path.exists():
            raise FileNotFoundError(f"ONNX model not found: {model_path}")

        self.session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        self.input_names = [i.name for i in self.session.get_inputs()]

    def rerank(self, query: str, docs: list[str], top_k: int) -> list[int]:
        import numpy as np

        # Prepare inputs (query, doc) pairs
        inputs = {}
        for name in self.input_names:
            if "query" in name.lower():
                inputs[name] = np.array([query] * len(docs), dtype=object)
            elif "doc" in name.lower() or "passage" in name.lower() or "text" in name.lower():
                inputs[name] = np.array(docs, dtype=object)

        scores = self.session.run(None, inputs)[0].flatten()
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return ranked[:top_k]

    def status(self) -> dict[str, Any]:
        return {"ok": True, "backend": "onnx_cross_encoder"}
