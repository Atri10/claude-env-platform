"""Coverage for the SessionStart/SessionEnd cost-tracking hook
(hooks/session_metrics_hook.py). Run: pytest tests/ -q
"""
import importlib
import importlib.util
import json
import os
import sys
import tempfile
from io import StringIO
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

_spec = importlib.util.spec_from_file_location(
    "session_metrics_hook", _ROOT / "hooks" / "session_metrics_hook.py")
smh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smh)


def _fresh_db():
    dsn = "sqlite:///" + tempfile.mktemp(suffix=".db")
    os.environ["CLAUDE_ENV_DSN"] = dsn
    import lib.db as db
    importlib.reload(db)
    d = db.get_db()
    d.apply_schema(str(_ROOT / "sql" / "001_schema.sql"))
    return d


def _run_hook(payload: dict, monkeypatch) -> int:
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps(payload)))
    return smh.main()


def _write_transcript(path: Path, n_messages: int = 3,
                      input_tokens: int = 100, output_tokens: int = 50) -> None:
    lines = []
    for i in range(n_messages):
        lines.append(json.dumps({
            "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}],
                       "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens}},
        }))
    lines.append(json.dumps({"message": {"role": "user", "content": "hi"}}))  # no usage, ignored
    path.write_text("\n".join(lines) + "\n")


def test_session_start_creates_row(tmp_path, monkeypatch):
    db = _fresh_db()
    repo = tmp_path / "myrepo"
    (repo / ".claude").mkdir(parents=True)
    (repo / ".claude" / "repo-policy.yaml").write_text("repo: myrepo\n")
    assert _run_hook({"session_id": "s1", "cwd": str(repo),
                      "hook_event_name": "SessionStart", "source": "startup"},
                     monkeypatch) == 0
    rows = db.query("SELECT * FROM metrics_sessions WHERE session_id=?", ("s1",))
    assert len(rows) == 1
    assert rows[0]["repo"] == "myrepo"
    assert rows[0]["ended_at"] is None


def test_session_end_sums_transcript_usage(tmp_path, monkeypatch):
    db = _fresh_db()
    transcript = tmp_path / "transcript.jsonl"
    _write_transcript(transcript, n_messages=3, input_tokens=100, output_tokens=50)
    assert _run_hook({"session_id": "s2", "cwd": str(tmp_path),
                      "transcript_path": str(transcript),
                      "hook_event_name": "SessionEnd", "reason": "other"},
                     monkeypatch) == 0
    rows = db.query("SELECT * FROM metrics_sessions WHERE session_id=?", ("s2",))
    assert rows[0]["input_tokens"] == 300     # 3 assistant messages * 100
    assert rows[0]["output_tokens"] == 150    # 3 * 50
    assert rows[0]["ended_at"] is not None
    assert rows[0]["est_cost_usd"] > 0


def test_session_end_is_idempotent(tmp_path, monkeypatch):
    db = _fresh_db()
    transcript = tmp_path / "transcript.jsonl"
    _write_transcript(transcript, n_messages=2, input_tokens=100, output_tokens=50)
    payload = {"session_id": "s3", "cwd": str(tmp_path), "transcript_path": str(transcript),
              "hook_event_name": "SessionEnd", "reason": "other"}
    _run_hook(payload, monkeypatch)
    _run_hook(payload, monkeypatch)  # fire twice
    rows = db.query("SELECT * FROM metrics_sessions WHERE session_id=?", ("s3",))
    assert rows[0]["input_tokens"] == 200      # not 400 -- overwrite, not increment


def test_malformed_transcript_line_does_not_crash(tmp_path, monkeypatch):
    db = _fresh_db()
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("not json\n" + json.dumps(
        {"message": {"role": "assistant", "content": "ok"}}) + "\n")  # no usage field
    assert _run_hook({"session_id": "s4", "cwd": str(tmp_path),
                      "transcript_path": str(transcript),
                      "hook_event_name": "SessionEnd", "reason": "other"},
                     monkeypatch) == 0
    rows = db.query("SELECT * FROM metrics_sessions WHERE session_id=?", ("s4",))
    assert rows[0]["input_tokens"] == 0


def test_malformed_usage_value_does_not_drop_whole_transcript(tmp_path, monkeypatch):
    db = _fresh_db()
    transcript = tmp_path / "transcript.jsonl"
    lines = [
        json.dumps({"message": {"role": "assistant", "content": "ok",
                                "usage": {"input_tokens": 100, "output_tokens": 50}}}),
        json.dumps({"message": {"role": "assistant", "content": "ok",
                                "usage": "not-a-dict"}}),                    # malformed usage
        json.dumps({"message": {"role": "assistant", "content": "ok",
                                "usage": {"input_tokens": "not-a-number", "output_tokens": 50}}}),
        json.dumps({"message": {"role": "assistant", "content": "ok",
                                "usage": {"input_tokens": 100, "output_tokens": 50}}}),
    ]
    transcript.write_text("\n".join(lines) + "\n")
    assert _run_hook({"session_id": "s6", "cwd": str(tmp_path),
                      "transcript_path": str(transcript),
                      "hook_event_name": "SessionEnd", "reason": "other"},
                     monkeypatch) == 0
    rows = db.query("SELECT * FROM metrics_sessions WHERE session_id=?", ("s6",))
    # the two malformed lines are skipped, not fatal -- the two good lines still count
    assert rows[0]["input_tokens"] == 200
    assert rows[0]["output_tokens"] == 100
    assert rows[0]["ended_at"] is not None


def test_missing_transcript_file_does_not_crash(monkeypatch):
    _fresh_db()
    assert _run_hook({"session_id": "s5", "cwd": "/tmp",
                      "transcript_path": "/nonexistent/path.jsonl",
                      "hook_event_name": "SessionEnd", "reason": "other"},
                     monkeypatch) == 0
