"""Regression coverage for ApprovalGate working with no registry_path. Run: pytest tests/ -q

Before this fix, ApprovalGate.__init__ unconditionally did
yaml.safe_load(registry_path.read_text()) against agents/agent_registry.yaml —
a file deleted when the agent-registry/task-router design was retired in
favor of native Claude Code .claude/agents/*.md files. That broke every live
caller of ApprovalGate: the approvals-ui web server's approve/deny POST
handler, and `approval_gate.py --resolve` on the CLI. Neither caller ever
used evaluate() (the only method that reads the registry) — they only use
open()/resolve()/list_open()/list_recent(), none of which touch it.
"""
import importlib
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


def _fresh_db():
    dsn = "sqlite:///" + tempfile.mktemp(suffix=".db")
    os.environ["CLAUDE_ENV_DSN"] = dsn
    import lib.db as db
    importlib.reload(db)
    d = db.get_db()
    d.apply_schema(str(_ROOT / "sql" / "001_schema.sql"))
    return d


def test_construction_with_no_registry_path_does_not_raise():
    _fresh_db()
    from agents.orchestration.approval_gate import ApprovalGate

    gate = ApprovalGate(session_id="test-no-registry")
    assert gate.registry_path is None
    assert gate.agents == {}
    assert gate.global_gates == []


def test_evaluate_without_registry_raises_a_clear_error():
    _fresh_db()
    from agents.orchestration.approval_gate import ApprovalGate

    gate = ApprovalGate(session_id="test-no-registry")
    try:
        gate.evaluate("backend", "filesystem.write", "src/app.py")
        assert False, "evaluate() should have raised without a registry_path"
    except RuntimeError as e:
        assert "registry_path" in str(e)


def test_open_and_resolve_work_with_no_registry():
    """This is the exact path approvals_ui.py and the CLI --resolve flag use."""
    _fresh_db()
    from agents.orchestration.approval_gate import ApprovalGate, GateVerdict

    gate = ApprovalGate(session_id="test-no-registry", repo="acme", tier=1,
                        actor="approvals-ui")
    verdict = GateVerdict(required=True, agent="backend", action="terminal.run",
                          target="rm -rf build/", tier=1,
                          reasons=["state-mutating terminal command"])
    req_id = gate.open(verdict)
    assert req_id

    open_rows = ApprovalGate.list_open()
    assert any(r["request_id"] == req_id for r in open_rows)

    gate.resolve(req_id, approved=True, decided_by="tester@localhost")

    recent = ApprovalGate.list_recent()
    resolved = [r for r in recent if r["request_id"] == req_id]
    assert resolved and resolved[0]["decision"] == "approved"


def test_approvals_ui_module_has_no_dangling_registry_constant():
    """REGISTRY used to point at the deleted agents/agent_registry.yaml."""
    import agents.orchestration.approvals_ui as ui
    assert not hasattr(ui, "REGISTRY")


def test_desktop_notification_feature_is_removed():
    """The macOS osascript toast (_notify) was removed 2026-07-09 — the web
    UI queue is the sole surface for pending approvals. open() must not depend
    on it, and the method must be gone."""
    from agents.orchestration.approval_gate import ApprovalGate
    assert not hasattr(ApprovalGate, "_notify")
