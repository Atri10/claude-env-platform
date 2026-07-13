"""
claude-env :: Adapters - Bootstrap Steps
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import venv as _venv
from pathlib import Path

from claudenv.ports.bootstrap import (
    BootstrapContext,
    BootstrapResult,
    IBootstrapStep,
)


class EnvironmentCheckStep:
    """Step 1: Check system environment (OS, Python, Git, Homebrew)."""

    @property
    def name(self) -> str:
        return "environment-check"

    @property
    def description(self) -> str:
        return "Verify platform, Python version, and required tools"

    def execute(self, context: BootstrapContext) -> BootstrapResult:
        warnings = []
        good = True

        sysname = platform.system()
        arch = platform.machine()
        if sysname == "Darwin" and arch == "arm64":
            pass  # OK
        elif sysname == "Linux":
            pass  # OK
        else:
            warnings.append(f"platform is {sysname}/{arch}; primary targets are macOS arm64 and Linux")
            good = False

        if sys.version_info >= (3, 13):
            pass  # OK
        else:
            warnings.append(f"python {platform.python_version()} < 3.13 required")
            good = False

        if not shutil.which("git"):
            warnings.append("git not found on PATH")
            good = False

        if not shutil.which("brew"):
            warnings.append("homebrew not found (needed for --with-brew)")

        message = "environment check passed" if good else "environment check has warnings"
        return BootstrapResult.ok(message, warnings) if good else BootstrapResult.failure(message, warnings)

    def can_skip(self, context: BootstrapContext) -> bool:
        return False  # Always run environment check


class DirectoryLayoutStep:
    """Step 2: Create directory layout under $CLAUDE_ENV_HOME."""

    @property
    def name(self) -> str:
        return "directory-layout"

    @property
    def description(self) -> str:
        return "Create platform directory structure"

    def execute(self, context: BootstrapContext) -> BootstrapResult:
        dirs = [
            "state", "venv",
            "knowledge", "knowledge/lancedb", "knowledge/docs",
            "models",
            "archive", "archive/memory", "archive/audit", "archive/security",
            "logs", "config", "bin",
        ]
        home = Path(context.home)
        for d in dirs:
            (home / d).mkdir(parents=True, exist_ok=True)
        return BootstrapResult.ok(f"directory layout created under {home}")

    def can_skip(self, context: BootstrapContext) -> bool:
        return False  # Idempotent, always run


class VenvCreationStep:
    """Step 3: Create Python virtual environment."""

    @property
    def name(self) -> str:
        return "venv-creation"

    @property
    def description(self) -> str:
        return "Create Python virtual environment (prefers uv)"

    def __init__(self, uv_available: bool):
        self._uv_available = uv_available

    def execute(self, context: BootstrapContext) -> BootstrapResult:
        venv_path = Path(context.venv_path)
        venv_py = Path(context.venv_python)

        if venv_py.exists() and not context.recreate_venv:
            return BootstrapResult.ok(f"venv already exists at {venv_path}")

        print(f"creating venv at {venv_path} ...")
        if self._uv_available:
            result = subprocess.run(
                ["uv", "venv", str(venv_path), "--python", "3.13"],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                print(f"uv venv failed: {result.stderr}; falling back to stdlib venv")
                _venv.create(str(venv_path), with_pip=True, clear=context.recreate_venv, symlinks=True)
        else:
            _venv.create(str(venv_path), with_pip=True, clear=context.recreate_venv, symlinks=True)

        return BootstrapResult.ok(f"venv created: {venv_py}")

    def can_skip(self, context: BootstrapContext) -> bool:
        if context.no_venv_create:
            return True
        venv_py = Path(context.venv_python)
        return venv_py.exists() and not context.recreate_venv


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


class DatabaseInitStep:
    """Step 5: Initialize SQLite database and apply schema."""

    @property
    def name(self) -> str:
        return "database-init"

    @property
    def description(self) -> str:
        return "Initialize SQLite database and apply migrations"

    def execute(self, context: BootstrapContext) -> BootstrapResult:
        # Import here to avoid circular imports
        from claudenv.adapters.config import get_config
        from claudenv.adapters.persistence import SQLiteDatabase

        config = get_config()
        dsn = context.dsn or config.get_database_dsn()
        db = SQLiteDatabase(dsn)

        repo_dir = Path(context.repo_dir)
        db.apply_schema(
            str(repo_dir / "sql" / "001_schema.sql"),
            str(repo_dir / "sql" / "002_retention.sql"),
            str(repo_dir / "sql" / "003_extensions.sql"),
            str(repo_dir / "sql" / "004_audit_trace_metadata.sql"),
        )
        return BootstrapResult.ok(f"database schema applied ({db.backend})")

    def can_skip(self, context: BootstrapContext) -> bool:
        return False  # Idempotent, always run


class LanceDBInitStep:
    """Step 6: Initialize LanceDB vector store."""

    @property
    def name(self) -> str:
        return "lancedb-init"

    @property
    def description(self) -> str:
        return "Initialize LanceDB vector database"

    def execute(self, context: BootstrapContext) -> BootstrapResult:
        path = Path(context.home) / "knowledge" / "lancedb"
        path.mkdir(parents=True, exist_ok=True)

        # Test lancedb import in the venv
        result = subprocess.run(
            [context.venv_python, "-c", "import lancedb; lancedb.connect('.')"],
            capture_output=True,
            cwd=str(path),
        )
        if result.returncode == 0:
            return BootstrapResult.ok(f"lancedb initialized at {path}")
        else:
            warnings = ["lancedb not importable in venv; directory created, check pip output above"]
            return BootstrapResult.ok(f"lancedb directory created at {path}", warnings)

    def can_skip(self, context: BootstrapContext) -> bool:
        return False  # Idempotent


class PolicyConfigStep:
    """Step 7: Deploy policy configs and mirror platform code."""

    @property
    def name(self) -> str:
        return "policy-config"

    @property
    def description(self) -> str:
        return "Deploy policy configs and mirror platform code"

    def _deploy_config(self, src: Path, dst: Path, label: str,
                       needed_by: str | None = None, force: bool = False) -> list[str]:
        warnings = []
        if not src.exists():
            suffix = f" ({needed_by} will fail until present)" if needed_by else ""
            warnings.append(f"{src.name} missing in repo{suffix}")
            return warnings
        if dst.exists() and not force:
            warnings.append(f"{label} -> {dst} (already present, left untouched; use --force-config to reset)")
            return warnings
        import shutil
        shutil.copy2(src, dst)
        return []

    def execute(self, context: BootstrapContext) -> BootstrapResult:
        warnings = []
        repo_dir = Path(context.repo_dir)
        home = Path(context.home)

        # Deploy global policy
        warnings.extend(self._deploy_config(
            repo_dir / "config" / "global-policy.yaml",
            home / "config" / "global-policy.yaml",
            "global policy",
            force=context.force_config,
        ))

        # Deploy repo-policy template
        tmpl = repo_dir / "config" / "repo-policy.template.yaml"
        dst_tmpl = home / "config" / "repo-policy.template.yaml"
        if tmpl.exists():
            import shutil
            shutil.copy2(tmpl, dst_tmpl)
            # ok

        # Deploy RAG model config
        warnings.extend(self._deploy_config(
            repo_dir / "config" / "rag.yaml",
            home / "config" / "rag.yaml",
            "rag model config",
            force=context.force_config,
        ))

        # Deploy configs needed by deployed scripts
        for name, needed_by in (("mcp-servers.json", "repo onboarding"),
                                 ("budgets.yaml", "claude-env budget")):
            warnings.extend(self._deploy_config(
                repo_dir / "config" / name,
                home / "config" / name,
                name,
                needed_by=needed_by,
                force=context.force_config,
            ))

        # Mirror platform code
        for sub in ("claudenv", "sql", "templates"):
            s = repo_dir / sub
            d = home / sub
            if s.exists():
                import shutil
                if d.exists():
                    shutil.rmtree(d)
                shutil.copytree(s, d, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"))

        # Clean stale scratch dirs
        removed = self._reap_stale_scratch(home)
        if removed:
            pass  # OK

        # Install CLI
        src_cli = repo_dir / "claudenv" / "bin" / "claude-env"
        if src_cli.exists():
            dst_cli = home / "bin" / "claude-env"
            import shutil
            shutil.copy2(src_cli, dst_cli)
            dst_cli.chmod(0o755)

        return BootstrapResult.ok(f"platform code mirrored under {home}", warnings)

    def _reap_stale_scratch(self, home: Path, ttl_hours: float = 24) -> list[str]:
        import time
        scratch_root = home / "scratch"
        if not scratch_root.is_dir():
            return []
        cutoff = time.time() - ttl_hours * 3600
        removed = []
        for child in scratch_root.iterdir():
            if child.is_dir() and child.stat().st_mtime < cutoff:
                import shutil
                shutil.rmtree(child, ignore_errors=True)
                removed.append(child.name)
        return removed

    def can_skip(self, context: BootstrapContext) -> bool:
        return False  # Idempotent with force flag


class AuditInitStep:
    """Step 8: Initialize audit ledger with genesis event."""

    @property
    def name(self) -> str:
        return "audit-init"

    @property
    def description(self) -> str:
        return "Initialize audit ledger with genesis event"

    def execute(self, context: BootstrapContext) -> BootstrapResult:
        from claudenv.adapters.config import get_config
        from claudenv.adapters.persistence import SQLiteDatabase
        from claudenv.adapters.audit import SqliteAuditLogger
        from claudenv.domain.value_objects import SessionId

        config = get_config()
        dsn = context.dsn or config.get_database_dsn()
        db = SQLiteDatabase(dsn)
        log = SqliteAuditLogger(
            db=db,
            session_id=SessionId.from_string("bootstrap"),
            actor="bootstrap",
        )
        log.agent_action(agent="bootstrap", action="initialize",
                         summary="platform bootstrap complete", success=True)
        result = log.verify_chain()
        if result.ok:
            return BootstrapResult.ok("audit chain initialized and verified")
        else:
            return BootstrapResult.failure(f"audit chain verification failed at event {result.broken_at}")

    def can_skip(self, context: BootstrapContext) -> bool:
        return False


class ValidationStep:
    """Step 9: Run validation script via venv Python."""

    @property
    def name(self) -> str:
        return "validation"

    @property
    def description(self) -> str:
        return "Run installation validation using venv Python"

    def execute(self, context: BootstrapContext) -> BootstrapResult:
        script = Path(context.repo_dir) / "claudenv" / "validation" / "validate_installation.py"
        if not script.exists():
            return BootstrapResult.ok("validate_installation.py not found; skipping final validation",
                                           ["validate_installation.py not found; skipping final validation"])

        result = subprocess.run([context.venv_python, str(script)])
        if result.returncode == 0:
            return BootstrapResult.ok("installation validation passed")
        else:
            return BootstrapResult.failure("installation validation failed")

    def can_skip(self, context: BootstrapContext) -> bool:
        return False


class ActivationHintStep:
    """Step 10: Print activation hints."""

    @property
    def name(self) -> str:
        return "activation-hint"

    @property
    def description(self) -> str:
        return "Print activation instructions"

    def execute(self, context: BootstrapContext) -> BootstrapResult:
        from pathlib import Path
        venv = Path(context.venv_path)
        try:
            rel = venv.relative_to(Path.home())
        except ValueError:
            rel = venv
        print(f"""
Venv location: {venv}
Venv Python:   {context.venv_python}

To use the venv directly (optional — the platform handles this automatically):
  source ~/{rel}/bin/activate

Or use the platform CLI (resolves the venv for you):
  {context.home}/bin/claude-env validate all
  {context.home}/bin/claude-env dashboard

To run any platform script manually:
  {context.venv_python} claudenv/validation/validate_installation.py
  {context.venv_python} claudenv/rag/bootstrap_rag.py /path/to/repo

Add to ~/.zshrc for convenience:
  export CLAUDE_ENV_HOME="{context.home}"
  export PATH="$CLAUDE_ENV_HOME/bin:$PATH"
""")
        return BootstrapResult.ok("activation hints printed")

    def can_skip(self, context: BootstrapContext) -> bool:
        return False