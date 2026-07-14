"""
claude-env :: Adapters - Bootstrap Steps - Activation Hint
"""
from __future__ import annotations

from pathlib import Path

from claudenv.ports.bootstrap import BootstrapContext, BootstrapResult


class ActivationHintStep:
    """Step 10: Print activation hints."""

    @property
    def name(self) -> str:
        return "activation-hint"

    @property
    def description(self) -> str:
        return "Print activation instructions"

    def execute(self, context: BootstrapContext) -> BootstrapResult:
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
