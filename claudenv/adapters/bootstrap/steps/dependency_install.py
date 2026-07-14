"""
claude-env :: Adapters - Bootstrap Steps - Dependency Install
"""
from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path

from claudenv.ports.bootstrap import BootstrapContext, BootstrapResult


class DependencyInstallStep:
    """Step 4: Install Python dependencies into the venv."""

    @property
    def name(self) -> str:
        return "dependency-install"

    @property
    def description(self) -> str:
        return "Install Python dependencies (locked versions)"

    def __init__(self, uv_available: bool):
        self._uv_available = uv_available

    # Locked dependencies (same as bootstrap.py)
    PIP_DEPS_LOCKED = [
        "pyyaml==6.0.3",
        "lancedb==0.33.0",
        "pyarrow==24.0.0",
        "llama-cpp-python==0.3.32",
        "onnxruntime==1.27.0",
        "transformers==5.12.1",
        "tree-sitter==0.26.0",
        "tree-sitter-language-pack==1.12.0",
        "mcp==1.28.1",
        "httpx==0.28.1",
        "datasette==0.65.2",
    ]

    BREW_FORMULAE = ["llama.cpp", "git", "sqlite"]

    def _pin_for(self, name: str) -> str:
        for spec in self.PIP_DEPS_LOCKED:
            if spec.split("==", 1)[0] == name:
                return spec
        return name

    def _pip_install(self, pkgs: list[str], *, force_reinstall: bool = False,
                     prefer_pip: bool = False, env: dict | None = None,
                     venv_python: str, venv_pip: str) -> int:
        if self._uv_available and not (prefer_pip and Path(venv_pip).exists()):
            cmd = ["uv", "pip", "install", "--python", venv_python, "--upgrade"]
        else:
            cmd = [venv_pip, "install", "--upgrade"]
        if force_reinstall:
            cmd.append("--force-reinstall")
        cmd.extend(pkgs)
        return subprocess.run(cmd, env=env).returncode

    def execute(self, context: BootstrapContext) -> BootstrapResult:
        warnings = []

        if not Path(context.venv_python).exists():
            return BootstrapResult.failure("venv not found — run without --no-venv-create first", warnings)

        if not self._uv_available and not Path(context.venv_pip).exists():
            return BootstrapResult.failure("venv pip not found and uv unavailable — recreate the venv", warnings)

        # Install bulk dependencies (excluding llama-cpp-python)
        bulk = [d for d in self.PIP_DEPS_LOCKED if not d.startswith("llama-cpp-python")]
        print(f"installing python dependencies into venv (via {'uv' if self._uv_available else 'pip'}) ...")
        if self._pip_install(bulk, venv_python=context.venv_python, venv_pip=context.venv_pip):
            warnings.append("dependency install reported errors; review output above")
        else:
            pass  # OK

        # Homebrew formulae
        if context.with_brew:
            if shutil.which("brew"):
                print("installing brew formulae ...")
                subprocess.run(["brew", "install", *self.BREW_FORMULAE])
            elif platform.system() == "Linux":
                warnings.append("--with-brew: no Homebrew on this host — install equivalents via package manager")

        # llama-cpp-python with Metal on Apple Silicon
        is_metal = platform.system() == "Darwin" and platform.machine() == "arm64"
        if is_metal:
            print("installing llama-cpp-python with Metal acceleration ...")
            env = {**__import__("os").environ, "CMAKE_ARGS": "-DLLAMA_METAL=on"}
        else:
            print("installing llama-cpp-python (CPU build) ...")
            env = None
        rc = self._pip_install(
            [self._pin_for("llama-cpp-python")],
            force_reinstall=True,
            prefer_pip=True,
            env=env,
            venv_python=context.venv_python,
            venv_pip=context.venv_pip,
        )
        if rc:
            warnings.append("llama-cpp-python install reported errors")
        else:
            pass  # OK

        msg = "python dependencies installed into venv"
        if warnings:
            return BootstrapResult.ok(msg, warnings)
        return BootstrapResult.ok(msg)

    def can_skip(self, context: BootstrapContext) -> bool:
        return context.no_deps
