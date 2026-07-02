"""Guards for the claude-env CLI dispatcher help. Run: pytest tests/ -q

Ensures every dispatched command carries a one-line description (so new commands
can't ship undocumented) and that the help paths render without error.
"""
import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

_CLI = Path(__file__).resolve().parents[1] / "bin" / "claude-env"
_loader = SourceFileLoader("claude_env_cli", str(_CLI))     # extensionless script
_spec = importlib.util.spec_from_loader("claude_env_cli", _loader)
cli = importlib.util.module_from_spec(_spec)
_loader.exec_module(cli)


def test_every_command_has_a_description():
    missing = [c for c in cli.CMDS if c not in cli.DESC]
    assert not missing, f"undocumented commands: {missing}"


def test_no_orphan_descriptions():
    orphans = [c for c in cli.DESC if c not in cli.CMDS and c != "validate"]
    assert not orphans, f"DESC entries with no matching command: {orphans}"


def test_overview_renders(capsys):
    cli.usage()
    out = capsys.readouterr().out
    assert "claude-env" in out
    assert "Onboard a repository" in out and "Diagnostics" in out


def test_cmd_help_paths(capsys):
    assert cli.cmd_help("onboard") == 0
    assert cli.cmd_help("validate") == 0
    assert cli.cmd_help("nope") == 2          # unknown -> nonzero
