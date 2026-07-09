"""Guard: subagents must declare explicit tool allow-lists; reviewer agents must be
read-only. Run: pytest tests/ -q

Subagents are contained only by their `tools:` allow-list (hard) and the MCP
servers — NOT by their prompt alone. Two invariants:

1. ALL agents must declare an explicit `tools:` allow-list (no allow-list means the
   agent inherits every tool, which is never intentional).

2. REVIEWER agents (name contains "reviewer") must be read-only — no Bash, Write,
   Edit, or NotebookEdit. This test fails if anyone reintroduces a shell/write-capable
   reviewer.

3. SPECIALIST agents (backend, frontend, devops, testing, documentation) may use write
   tools — they are implementation agents, not review agents. The orchestrator may use
   Agent. The constraint is only that they declare an allow-list (invariant 1).
"""
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_WRITE_TOOLS = {"Bash", "Write", "Edit", "NotebookEdit"}
_AGENT_DIRS = [
    _ROOT / ".claude" / "agents",
    _ROOT / "templates" / "repo-onboarding" / ".claude" / "agents",
]


def _agent_files():
    return sorted(f for d in _AGENT_DIRS if d.exists() for f in d.glob("*.md"))


def _tools_of(path: Path) -> set:
    m = re.search(r"(?m)^tools:\s*(.+)$", path.read_text())
    return {t.strip() for t in m.group(1).split(",") if t.strip()} if m else set()


def test_agent_files_exist():
    assert _agent_files(), "no .claude/agents/*.md found — did the paths move?"


def test_all_agents_declare_tools_allowlist():
    """Every agent must declare an explicit tools: allow-list — no silent inherit-all."""
    missing = {}
    for f in _agent_files():
        tools = _tools_of(f)
        if not tools:
            missing[str(f.relative_to(_ROOT))] = "<missing tools: allow-list>"
    assert not missing, (
        f"agents must declare an explicit tools: allow-list. Missing: {missing}")


def test_reviewer_subagents_are_read_only():
    """Agents whose filename contains 'reviewer' must be read-only (no write tools)."""
    offenders = {}
    for f in _agent_files():
        if "reviewer" not in f.stem:
            continue  # only reviewer agents are constrained to read-only
        tools = _tools_of(f)
        bad = tools & _WRITE_TOOLS
        if bad:
            offenders[str(f.relative_to(_ROOT))] = sorted(bad)
    assert not offenders, (
        f"reviewer subagents must be read-only — no {sorted(_WRITE_TOOLS)}. "
        f"Offenders: {offenders}")
