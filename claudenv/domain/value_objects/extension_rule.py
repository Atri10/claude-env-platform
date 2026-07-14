"""
claude-env :: Domain - Value Objects - ExtensionRule
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExtensionRule:
    """File extension matching rule."""
    extension: str  # e.g., ".env", ".pem"

    def matches(self, path: str) -> bool:
        from pathlib import Path as PathLib
        path_obj = PathLib(path)
        suffix = path_obj.suffix
        name = path_obj.name

        # Normal extension match
        if suffix == self.extension:
            return True
        # Exact filename match (e.g., ".env")
        if name == self.extension:
            return True
        # Compound extensions (e.g., ".env.local")
        if name.startswith(self.extension + "."):
            return True
        # Dotfile variants (e.g., "..env")
        bare_name = name.lstrip(".")
        bare_ext = self.extension.lstrip(".")
        if bare_name == bare_ext:
            return True
        if bare_name.startswith(bare_ext + "."):
            return True
        return False
