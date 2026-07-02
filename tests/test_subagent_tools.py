"""Guard: review/analysis subagents must stay read-only. Run: pytest tests/ -q

Subagents are contained only by their `tools:` allow-list (hard) and the MCP
servers (hard, caller-independent) — NOT by their prompt, and NOT reliably by the
PreToolUse policy hook (whether it fires on a subagent's tool calls is
undocumented in Claude Code). The shell is the native-tool bypass surface, so no
`.claude/agents/*.md` (platform or onboarding template) may grant Bash, Write,
Edit, or NotebookEdit, and each must declare an explicit read-only allow-list.
This test fails if anyone reintroduces a shell/write-capable reviewer.
"""
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_FORBIDDEN = {"Bash", "Write", "Edit", "NotebookEdit"}
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


def test_reviewer_subagents_are_read_only():
    offenders = {}
    for f in _agent_files():
        tools = _tools_of(f)
        if not tools:                       # no allow-list => inherits everything
            offenders[str(f.relative_to(_ROOT))] = ["<missing tools: allow-list>"]
        elif tools & _FORBIDDEN:
            offenders[str(f.relative_to(_ROOT))] = sorted(tools & _FORBIDDEN)
    assert not offenders, (
        f"subagents must be read-only — no {sorted(_FORBIDDEN)} and must declare a "
        f"tools: allow-list. Offenders: {offenders}")
