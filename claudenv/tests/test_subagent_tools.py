"""
Enforces the least-privilege invariant for governance / analysis subagents.

Per `claudenv/CLAUDE.md` and `.claude/agents/governance-reviewer.md`, any
reviewer or analysis subagent must be **read-only by construction**: its
frontmatter `tools:` may only contain `Read` / `Grep` / `Glob`, and must never
grant `Bash` / `Write` / `Edit` / `NotebookEdit` -- the native-tool shell/write
bypass surface. The subagent is contained only by its tool allow-list (and the
MCP servers), not by its prompt, so a reviewer granted a shell is a governance
violation.

This test is the regression guard referenced by `governance-reviewer.md`.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

AGENTS_DIR = Path(__file__).resolve().parents[2] / ".claude" / "agents"

# The native-tool shell / write bypass surface. Granting any of these to a
# read-only reviewer subagent is a hard finding per governance-reviewer.md.
DANGEROUS_TOOLS = {"Bash", "Write", "Edit", "NotebookEdit"}
ALLOWED_REVIEWER_TOOLS = {"Read", "Grep", "Glob"}


def _load_frontmatter(path: Path) -> dict:
    text = path.read_text()
    if not text.startswith("---"):
        pytest.fail(f"{path.name}: expected YAML frontmatter (starts with '---')")
    _, _, rest = text.partition("---")
    body, _, _ = rest.partition("---")
    return yaml.safe_load(body) or {}


def _reviewer_paths() -> list[Path]:
    """Discover reviewer/analysis subagent files from the agents directory."""
    if not AGENTS_DIR.exists():
        return []
    found = []
    for p in sorted(AGENTS_DIR.glob("*.md")):
        fm = _load_frontmatter(p)
        name = str(fm.get("name", ""))
        desc = str(fm.get("description", ""))
        # A reviewer/analysis subagent: name ends with "reviewer", or the
        # description explicitly describes a read-only reviewer.
        if name.endswith("reviewer") or ("read-only" in desc and "review" in desc):
            found.append(p)
    return found


def _tool_set(tools) -> set[str]:
    if tools is None:
        return set()
    if isinstance(tools, str):
        return {t.strip() for t in tools.split(",") if t.strip()}
    return set(tools)


class TestReviewerSubagentToolGrants:
    @pytest.mark.parametrize(
        "agent_path", _reviewer_paths(), ids=lambda p: p.stem
    )
    def test_reviewer_is_read_only(self, agent_path: Path):
        fm = _load_frontmatter(agent_path)
        tools = _tool_set(fm.get("tools"))
        assert tools, f"{agent_path.name}: reviewer subagent must declare a `tools` field"

        # No shell/write bypass surface may be granted to a reviewer.
        leaked = tools & DANGEROUS_TOOLS
        assert not leaked, (
            f"{agent_path.name}: reviewer grants dangerous tools {sorted(leaked)}; "
            f"reviewers must be read-only (Read/Grep/Glob)"
        )

        # A reviewer should be confined to the read-only tool set.
        assert tools <= ALLOWED_REVIEWER_TOOLS, (
            f"{agent_path.name}: reviewer tools {sorted(tools)} exceed the read-only "
            f"set {sorted(ALLOWED_REVIEWER_TOOLS)}"
        )
