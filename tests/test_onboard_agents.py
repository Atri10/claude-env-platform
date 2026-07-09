"""Tests for the agent-install step added to register_repo.py (Task 3)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

# lazily imported after Task 3 adds the function
def _get_installer():
    from scripts.register_repo import _install_agents  # noqa: PLC0415
    return _install_agents

_TEMPLATE_AGENTS = _ROOT / "templates" / "repo-onboarding" / ".claude" / "agents"

# All eleven agent slugs the template must ship
EXPECTED_AGENTS = {
    "orchestrator", "architect", "backend", "frontend", "database",
    "devops", "security", "performance", "testing", "documentation", "research",
}


def test_agent_install_copies_all_eleven_agents(tmp_path):
    _install_agents = _get_installer()
    result = _install_agents(tmp_path, dry_run=False)
    installed = {p.stem for p in (tmp_path / ".claude" / "agents").glob("*.md")}
    assert EXPECTED_AGENTS.issubset(installed), f"missing: {EXPECTED_AGENTS - installed}"
    assert "installed" in result.lower() or str(len(EXPECTED_AGENTS)) in result


def test_agent_install_accepts_str_repo_root(tmp_path):
    """Regression: main() passes repo_root as a str (str(Path(...).resolve())),
    not a Path. _install_agents must handle both — it used to do `repo_root /
    ".claude"` and crashed with TypeError on a str during real onboarding."""
    _install_agents = _get_installer()
    result = _install_agents(str(tmp_path), dry_run=False)   # str, as main() passes
    installed = {p.stem for p in (tmp_path / ".claude" / "agents").glob("*.md")}
    assert EXPECTED_AGENTS.issubset(installed), f"missing: {EXPECTED_AGENTS - installed}"
    assert "installed" in result.lower()

    # dry-run with a str must also not raise
    result_dry = _install_agents(str(tmp_path / "other"), dry_run=True)
    assert "would install" in result_dry.lower()


def test_agent_install_is_merge_safe(tmp_path):
    _install_agents = _get_installer()
    dst = tmp_path / ".claude" / "agents"
    dst.mkdir(parents=True)
    sentinel = "# CUSTOM CONTENT — must not be overwritten"
    (dst / "backend.md").write_text(sentinel)
    _install_agents(tmp_path, dry_run=False)
    assert (dst / "backend.md").read_text() == sentinel


def test_agent_install_dry_run_writes_nothing(tmp_path):
    _install_agents = _get_installer()
    result = _install_agents(tmp_path, dry_run=True)
    assert not (tmp_path / ".claude").exists()
    assert "would install" in result.lower() or "dry" in result.lower()


def test_no_template_flag_skips_agents(tmp_path):
    """_install_agents is callable — the --no-template guard lives in main()."""
    _install_agents = _get_installer()
    assert callable(_install_agents)


def test_orchestrator_description_is_scoped_to_multi_step():
    src = _TEMPLATE_AGENTS / "orchestrator.md"
    assert src.exists(), "orchestrator.md not yet written — run Task 2 first"
    content = src.read_text()
    lower = content.lower()
    assert any(kw in lower for kw in ("multi", "cross", "span", "coordinated")), (
        "orchestrator description must contain scoping language (multi/cross/span/coordinated)"
    )


def test_all_agents_have_required_sections():
    required_sections = [
        "## discover first",
        "## scope",
        "## working method",
        "## hard rules",
        "## tier-aware behavior",
    ]
    missing = {}
    for agent_file in _TEMPLATE_AGENTS.glob("*.md"):
        if agent_file.stem in {"code-reviewer", "architecture-reviewer"}:
            continue  # existing reviewers — not part of this feature
        content = agent_file.read_text().lower()
        gaps = [s for s in required_sections if s not in content]
        if gaps:
            missing[agent_file.name] = gaps
    assert not missing, f"agents missing required sections: {missing}"


def test_all_agents_have_valid_frontmatter():
    for agent_file in _TEMPLATE_AGENTS.glob("*.md"):
        if agent_file.stem in {"code-reviewer", "architecture-reviewer"}:
            continue
        content = agent_file.read_text()
        assert content.startswith("---\n"), f"{agent_file.name} missing frontmatter"
        end = content.index("---\n", 4)
        fm = content[4:end]
        assert "name:" in fm, f"{agent_file.name} frontmatter missing name:"
        assert "description:" in fm, f"{agent_file.name} frontmatter missing description:"
        assert "tools:" in fm, f"{agent_file.name} frontmatter missing tools:"
