"""
claude-env :: Application - Model Manager

Orchestrates model recommendation, download, and configuration.
Provides the one-click ``claude-env model ensure`` experience.
"""
from __future__ import annotations

import json
import logging
import os
import ssl
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import certifi
import yaml

from claudenv._data import config_dir

logger = logging.getLogger(__name__)

_MODEL_CATALOG: dict[str, Any] | None = None


def _load_catalog() -> dict[str, Any]:
    global _MODEL_CATALOG
    if _MODEL_CATALOG is None:
        catalog_path = config_dir() / "model-catalog.json"
        _MODEL_CATALOG = json.loads(catalog_path.read_text())
    return _MODEL_CATALOG


@dataclass
class ModelStatus:
    configured: bool
    model_path: str = ""
    backend: str = ""
    dim: int = 0


@dataclass
class DownloadResult:
    success: bool
    model_path: str = ""
    size_bytes: int = 0
    message: str = ""


class ModelManager:
    """Checks, recommends, and downloads embedding/reranker models."""

    def __init__(self, home: Path | str | None = None):
        self.home = Path(home or os.environ.get("CLAUDE_ENV_HOME", Path.home() / ".claude-env"))
        self.config_dir = self.home / "config"
        self.models_dir = self.home / "models"
        self.models_dir.mkdir(parents=True, exist_ok=True)

    def status(self, model_type: str = "embedding") -> ModelStatus:
        """Check if a model is configured and present on disk."""
        rag_yaml = self.config_dir / "rag.yaml"
        if not rag_yaml.exists():
            return ModelStatus(configured=False)

        config = yaml.safe_load(rag_yaml.read_text()) or {}

        if model_type == "embedding":
            emb = config.get("embedding", {})
            model_path = emb.get("model_path", "")
            backend = emb.get("backend", "")
            if backend not in ("llama_cpp", "onnx", "dummy"):
                return ModelStatus(configured=False)
            if model_path and Path(os.path.expanduser(model_path)).exists():
                return ModelStatus(
                    configured=True,
                    model_path=model_path,
                    backend=emb.get("backend", ""),
                    dim=emb.get("embedding_dim", 0),
                )
            return ModelStatus(configured=False)
        elif model_type == "reranker":
            rer = config.get("reranker", {})
            model_dir = rer.get("model_dir", "")
            if model_dir and Path(os.path.expanduser(model_dir)).exists():
                return ModelStatus(
                    configured=True,
                    model_path=model_dir,
                    backend=rer.get("backend", ""),
                )
            return ModelStatus(configured=False)
        else:
            return ModelStatus(configured=False)

    def ensure_model(
        self,
        model_type: str = "embedding",
        progress_callback=None,
    ) -> DownloadResult:
        """Ensure a model is configured, downloading if needed.

        Checks the specific model_type (embedding/reranker). If already
        configured and the files exist on disk, returns immediately.
        Otherwise downloads the recommended model and updates rag.yaml.
        """
        status = self.status(model_type)
        if status.configured:
            return DownloadResult(
                success=True,
                model_path=status.model_path,
                message=f"{model_type} model already configured: {status.model_path}",
            )

        catalog = _load_catalog()
        models = catalog.get(model_type, {})
        recommended = models.get("recommended", {})

        if not recommended:
            return DownloadResult(
                success=False,
                message=f"No recommended {model_type} model in catalog. Configure rag.yaml manually.",
            )

        return self._download_model(recommended, model_type, progress_callback)

    def _download_model(
        self,
        model_info: dict,
        model_type: str,
        progress_callback=None,
    ) -> DownloadResult:
        name = model_info["name"]
        model_dir = self.models_dir / name
        model_dir.mkdir(parents=True, exist_ok=True)

        url = model_info["url"]
        fname = url.rsplit("/", 1)[-1].split("?")[0]  # derive filename from URL
        files_to_download = [(fname, url)]
        if "tokenizer_url" in model_info:
            files_to_download.append(("tokenizer.json", model_info["tokenizer_url"]))

        total_size = 0
        for fname, url in files_to_download:
            dest = model_dir / fname
            if not dest.exists():
                try:
                    size = self._download_file(url, dest, progress_callback)
                    total_size += size
                except Exception as exc:
                    logger.exception("failed to download %s from %s", fname, url)
                    msg = str(exc)
                    if "CERTIFICATE_VERIFY_FAILED" in msg:
                        msg = (
                            "SSL certificate verification failed. "
                            "Run: pip install --upgrade certifi"
                        )
                    elif "Connection refused" in msg or "getaddrinfo" in msg:
                        msg = "Network connection failed — check your internet connection."
                    return DownloadResult(
                        success=False, message=f"Download failed for {fname}: {msg}",
                    )
            else:
                logger.debug("model file %s already exists, skipping", fname)

        model_path = str(model_dir / fname)
        self._update_rag_config(model_info, model_type, model_path)
        self._record_download(model_info, model_type, model_path, total_size)

        return DownloadResult(
            success=True,
            model_path=model_path,
            size_bytes=total_size,
            message=f"Model {name} configured successfully at {model_path}",
        )

    def _download_file(
        self, url: str, dest: Path, progress_callback=None,
    ) -> int:
        """Download a file with optional progress reporting. Returns size in bytes.

        ``progress_callback``, if given, is called as
        ``progress_callback(bytes_so_far, total_bytes)`` after every chunk.
        The first call has ``bytes_so_far == 0`` so the caller can initialise
        a progress bar keyed on ``total_bytes``."""
        logger.info("downloading %s -> %s", url, dest)

        ctx = ssl.create_default_context(cafile=certifi.where())
        req = urllib.request.Request(url, headers={"User-Agent": "claude-env/1.0"})
        with urllib.request.urlopen(req, context=ctx, timeout=300) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            if progress_callback and total:
                progress_callback(0, total)
            downloaded = 0
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total:
                        progress_callback(downloaded, total)

        file_size = dest.stat().st_size
        logger.info("download complete: %s (%d bytes)", dest.name, file_size)
        return file_size

    def _update_rag_config(
        self, model_info: dict, model_type: str, model_path: str,
    ) -> None:
        """Write the model path and settings into the deployed rag.yaml."""
        rag_yaml = self.config_dir / "rag.yaml"
        if rag_yaml.exists():
            config = yaml.safe_load(rag_yaml.read_text()) or {}
        else:
            # Copy packaged default first
            packaged = config_dir() / "rag.yaml"
            config = yaml.safe_load(packaged.read_text()) if packaged.exists() else {}

        if model_type == "embedding":
            config.setdefault("embedding", {})
            config["embedding"]["model_path"] = model_path
            config["embedding"]["backend"] = model_info.get("backend", "llama_cpp")
            config["embedding"]["embedding_dim"] = model_info.get("dim", 768)
            config["embedding"]["pooling_type"] = model_info.get("pooling_type", "mean")
            config["embedding"]["model_name"] = model_info.get("name", "")
        elif model_type == "reranker":
            config.setdefault("reranker", {})
            config["reranker"]["model_dir"] = model_path
            config["reranker"]["backend"] = model_info.get("backend", "onnx_cross_encoder")

        rag_yaml.write_text(yaml.safe_dump(config, sort_keys=False, default_flow_style=False))
        logger.info("rag.yaml updated with %s model at %s", model_type, model_path)

    def _record_download(
        self, model_info: dict, model_type: str, model_path: str, size_bytes: int,
    ) -> None:
        """Record the download in the operational database for audit."""
        from claudenv.domain.value_objects._time import iso_now

        try:
            from claudenv.adapters.config import get_config
            from claudenv.adapters.persistence import SQLiteDatabase

            config = get_config()
            db = SQLiteDatabase(config.get_database_dsn())
            db.execute(
                """INSERT INTO model_downloads
                   (model_name, model_type, backend, source_url, file_path,
                    size_bytes, downloaded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    model_info["name"], model_type, model_info.get("backend", ""),
                    model_info.get("url", ""), model_path, size_bytes, iso_now(),
                ),
            )
            db.close()
        except Exception:
            logger.warning("failed to record model download in DB (non-fatal)", exc_info=True)
