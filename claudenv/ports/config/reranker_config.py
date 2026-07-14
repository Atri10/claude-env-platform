"""
claude-env :: Ports - Reranker configuration value object
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RerankerConfig:
    backend: str = "onnx_cross_encoder"
    model_dir: str = ""
