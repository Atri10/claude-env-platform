# Session Cost Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `metrics_sessions` (and therefore `claude-env budget` and the dashboard) populate itself automatically — no user configuration, no cron job, no notification — by recording cost at `SessionStart`/`SessionEnd`.

**Architecture:** A new native hook (`hooks/session_metrics_hook.py`) registers on Claude Code's `SessionStart`/`SessionEnd` events via the existing `hooks/install_hooks.py` install path. `SessionStart` opens a `metrics_sessions` row; `SessionEnd` sums token usage straight out of that session's own transcript JSONL and overwrites the row's totals. `observability/collectors.py` gains one new absolute-overwrite writer. `observability/budgets.py` drops its unreliable macOS notification. `observability/dashboard.py` gains a per-repo cost total.

**Tech Stack:** Python 3 stdlib only (`json`, `pathlib`), existing `lib.db.get_db()` / `lib.repo_policy.repo_slug()` / `pytest`.

## Global Constraints

- No module imports `sqlite3`/`psycopg` directly — all DB access via `lib.db.get_db()` (`?` placeholders only).
- Hook scripts must never raise or block the session — wrap the whole body in `try/except Exception: pass` and always `return 0` (see `hooks/audit_hook.py` for the established pattern).
- `metrics_sessions` writes must be idempotent — a hook firing twice for the same `session_id` must not double-count cost (`SessionEnd` **overwrites** absolute totals, it never increments).
- No new CLI flags, no new config file, no scheduled job — the feature must work with zero user setup beyond the existing `claude-env hooks` install step.
- Every behavioral change gets a test in `tests/`; run `pytest tests/ -q` after every task.
- Branch first (already on `feat/session-cost-tracking`); commit after every task with the `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>` trailer.
- After all tasks, deploy every changed file to `$CLAUDE_ENV_HOME` per `CLAUDE.md` golden rule 2 before considering this done.

---

### Task 1: `collectors.py` — add `set_usage_totals()`

**Files:**
- Modify: `observability/collectors.py` (add function after `record_tokens`, then delete `record_tokens` — it becomes dead code once `SessionEnd` sums the whole transcript instead of incrementing per tool call)
- Test: Create `tests/test_collectors.py`

**Interfaces:**
- Consumes: `lib.db.get_db()` (existing), `PRICES` dict (existing, in `observability/collectors.py`)
- Produces: `observability.collectors.set_usage_totals(session_id: str, input_tokens: int, output_tokens: int, model: str = "default") -> None` — overwrites (not increments) `metrics_sessions.input_tokens`/`output_tokens`/`est_cost_usd` for that `session_id`. Used by Task 2's hook.

- [ ] **Step 1: Write the failing test**

Create `tests/test_collectors.py`:

```python
"""Coverage for observability/collectors.py session-cost writers.
Run: pytest tests/ -q
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


def test_start_session_creates_row_once():
    db = _fresh_db()
    from observability.collectors import start_session
    start_session("sess-1", "myrepo")
    start_session("sess-1", "myrepo")  # second call must not error or duplicate
    rows = db.query("SELECT * FROM metrics_sessions WHERE session_id=?", ("sess-1",))
    assert len(rows) == 1
    assert rows[0]["repo"] == "myrepo"


def test_set_usage_totals_overwrites_not_increments():
    db = _fresh_db()
    from observability.collectors import start_session, set_usage_totals
    start_session("sess-2", "myrepo")
    set_usage_totals("sess-2", 1000, 500)
    set_usage_totals("sess-2", 1200, 600)  # simulate the hook firing twice
    rows = db.query("SELECT * FROM metrics_sessions WHERE session_id=?", ("sess-2",))
    assert rows[0]["input_tokens"] == 1200   # overwritten, not 1000+1200
    assert rows[0]["output_tokens"] == 600
    expected_cost = (1200 * 3.00 + 600 * 15.00) / 1_000_000
    assert abs(rows[0]["est_cost_usd"] - expected_cost) < 1e-9


def test_end_session_sets_ended_at():
    db = _fresh_db()
    from observability.collectors import start_session, end_session
    start_session("sess-3", "myrepo")
    end_session("sess-3")
    rows = db.query("SELECT ended_at FROM metrics_sessions WHERE session_id=?", ("sess-3",))
    assert rows[0]["ended_at"] is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_collectors.py -v`
Expected: FAIL on `test_set_usage_totals_overwrites_not_increments` with `ImportError: cannot import name 'set_usage_totals'`.

- [ ] **Step 3: Add `set_usage_totals`, remove `record_tokens`**

In `observability/collectors.py`, replace the existing `record_tokens` function:

```python
def record_tokens(session_id: str, input_tokens: int, output_tokens: int,
                  model: str = "default") -> None:
    pin, pout = PRICES.get(model, PRICES["default"])
    cost = (input_tokens * pin + output_tokens * pout) / 1_000_000
    get_db().execute(
        "UPDATE metrics_sessions SET input_tokens=input_tokens+?, "
        "output_tokens=output_tokens+?, est_cost_usd=est_cost_usd+? WHERE session_id=?",
        (input_tokens, output_tokens, cost, session_id))
```

with:

```python
def set_usage_totals(session_id: str, input_tokens: int, output_tokens: int,
                     model: str = "default") -> None:
    """Overwrite (not increment) a session's usage/cost totals. Called once
    per SessionEnd with the transcript's full cumulative usage — idempotent
    if the hook ever fires more than once for the same session."""
    pin, pout = PRICES.get(model, PRICES["default"])
    cost = (input_tokens * pin + output_tokens * pout) / 1_000_000
    get_db().execute(
        "UPDATE metrics_sessions SET input_tokens=?, output_tokens=?, "
        "est_cost_usd=? WHERE session_id=?",
        (input_tokens, output_tokens, cost, session_id))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_collectors.py -v`
Expected: 3 passed.

- [ ] **Step 5: Run the full suite (nothing else references `record_tokens`)**

Run: `grep -rn "record_tokens" . --include="*.py"`
Expected: no output (confirms it's safe to delete — if anything shows up, stop and investigate before proceeding).

Run: `python3 -m pytest tests/ -q`
Expected: all pass (no new failures elsewhere).

- [ ] **Step 6: Commit**

```bash
git add observability/collectors.py tests/test_collectors.py
git commit -m "$(cat <<'EOF'
feat(observability): add set_usage_totals, drop unused record_tokens

record_tokens incremented cost per call, but nothing ever called it in
practice. SessionEnd will sum a transcript's full cumulative usage in one
shot (Task 2), so an absolute overwrite is the right primitive, not an
increment.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `hooks/session_metrics_hook.py` — the new hook

**Files:**
- Create: `hooks/session_metrics_hook.py`
- Test: Create `tests/test_session_metrics_hook.py`

**Interfaces:**
- Consumes: `observability.collectors.start_session(session_id, repo)`, `.set_usage_totals(session_id, input_tokens, output_tokens)`, `.end_session(session_id)` (Task 1); `lib.repo_policy.repo_slug(cwd, fallback_to_basename=True)` (existing).
- Produces: `hooks/session_metrics_hook.py::main() -> int`, reading stdin JSON `{session_id, transcript_path, cwd, hook_event_name}` — consumed by Task 3's install wiring.

- [ ] **Step 1: Write the failing test**

Create `tests/test_session_metrics_hook.py`:

```python
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


def test_missing_transcript_file_does_not_crash(monkeypatch):
    _fresh_db()
    assert _run_hook({"session_id": "s5", "cwd": "/tmp",
                      "transcript_path": "/nonexistent/path.jsonl",
                      "hook_event_name": "SessionEnd", "reason": "other"},
                     monkeypatch) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_session_metrics_hook.py -v`
Expected: FAIL — `FileNotFoundError` / `ImportError` because `hooks/session_metrics_hook.py` does not exist yet.

- [ ] **Step 3: Write the hook**

Create `hooks/session_metrics_hook.py`:

```python
#!/usr/bin/env python3
"""
claude-env :: Claude Code SessionStart/SessionEnd cost-tracking hook
File: hooks/session_metrics_hook.py
Purpose:
    Populate metrics_sessions automatically, with zero user configuration.
    SessionStart opens a row (session_id, repo, started_at); SessionEnd sums
    token usage straight out of the session's own transcript and writes the
    final cost + ended_at. Replaces the old dependency on manually-scheduled
    session ingestion, which never wrote cost data in the first place.

Protocol: stdin JSON {session_id, transcript_path, cwd, hook_event_name, ...}
(SessionStart also carries `source`; SessionEnd also carries `reason` — this
hook does not filter on either, it runs the same for every source/reason).
Never blocks anything; never fails loudly.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HOME = Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))
for p in (HOME, Path(__file__).resolve().parents[1]):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def _repo_slug(cwd: str) -> str | None:
    from lib.repo_policy import repo_slug
    return repo_slug(cwd, fallback_to_basename=True)


def _sum_transcript_usage(transcript_path: str) -> tuple[int, int]:
    """Sum input/output tokens across every assistant message in the
    transcript. Malformed lines, a missing file, or a message with no
    usage field all count as 0 rather than raising."""
    total_in = total_out = 0
    if not transcript_path:
        return 0, 0
    p = Path(transcript_path)
    if not p.exists():
        return 0, 0
    with p.open(errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            msg = rec.get("message") or {}
            if msg.get("role") != "assistant":
                continue
            usage = msg.get("usage") or {}
            total_in += int(usage.get("input_tokens") or 0)
            total_out += int(usage.get("output_tokens") or 0)
    return total_in, total_out


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        event = payload.get("hook_event_name", "")
        session = payload.get("session_id", "hook")
        cwd = payload.get("cwd", "")

        from observability.collectors import start_session, set_usage_totals, end_session

        if event == "SessionStart":
            start_session(session, _repo_slug(cwd))
        elif event == "SessionEnd":
            start_session(session, _repo_slug(cwd))  # ensure a row exists either way
            total_in, total_out = _sum_transcript_usage(payload.get("transcript_path", ""))
            set_usage_totals(session, total_in, total_out)
            end_session(session)
    except Exception:
        pass  # cost tracking must never disturb the session
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_session_metrics_hook.py -v`
Expected: 5 passed.

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add hooks/session_metrics_hook.py tests/test_session_metrics_hook.py
git commit -m "$(cat <<'EOF'
feat(hooks): add SessionStart/SessionEnd cost-tracking hook

New hooks/session_metrics_hook.py opens a metrics_sessions row on
SessionStart and sums transcript token usage into it on SessionEnd,
following the same never-block/never-raise shape as audit_hook.py. Not
wired into install_hooks.py yet (Task 3).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `hooks/install_hooks.py` — wire the new hook in

**Files:**
- Modify: `hooks/install_hooks.py:39-46` (constants), `:111-124` (main install/uninstall logic)
- Modify: `tests/test_install_hooks.py` (extend `_all_commands` and add new assertions)

**Interfaces:**
- Consumes: `hooks/session_metrics_hook.py` (Task 2, referenced by filename only).
- Produces: `claude-env hooks` now also registers/uninstalls `SessionStart`/`SessionEnd` entries — no new function signatures for later tasks to consume.

- [ ] **Step 1: Write the failing test additions**

In `tests/test_install_hooks.py`, replace the `_all_commands` helper:

```python
def _all_commands(hooks: dict) -> list[str]:
    cmds = []
    for event in ("PreToolUse", "PostToolUse"):
        for entry in hooks.get(event, []):
            for h in entry.get("hooks", []):
                cmds.append(h.get("command", ""))
    return cmds
```

with a version that also covers the two new events:

```python
def _all_commands(hooks: dict) -> list[str]:
    cmds = []
    for event in ("PreToolUse", "PostToolUse", "SessionStart", "SessionEnd"):
        for entry in hooks.get(event, []):
            for h in entry.get("hooks", []):
                cmds.append(h.get("command", ""))
    return cmds
```

Then append these new test functions to the end of the file:

```python
def test_session_hooks_installed(tmp_path, monkeypatch):
    assert _run(["--repo", str(tmp_path)], monkeypatch) == 0
    data = _hooks(tmp_path / ".claude" / "settings.json")
    assert any("session_metrics_hook.py" in c
               for e in data.get("SessionStart", []) for h in e.get("hooks", []))
    assert any("session_metrics_hook.py" in c for c in _all_commands(data))


def test_session_hooks_removed_on_uninstall(tmp_path, monkeypatch):
    _run(["--repo", str(tmp_path)], monkeypatch)
    _run(["--repo", str(tmp_path), "--uninstall"], monkeypatch)
    data = _hooks(tmp_path / ".claude" / "settings.json")
    assert not data.get("SessionStart")
    assert not data.get("SessionEnd")


def test_session_hooks_idempotent(tmp_path, monkeypatch):
    _run(["--repo", str(tmp_path)], monkeypatch)
    _run(["--repo", str(tmp_path)], monkeypatch)
    data = _hooks(tmp_path / ".claude" / "settings.json")
    cmds = [h.get("command", "") for e in data.get("SessionStart", []) for h in e.get("hooks", [])]
    assert sum("session_metrics_hook.py" in c for c in cmds) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_install_hooks.py -v`
Expected: FAIL on `test_session_hooks_installed` (no `SessionStart` key written).

- [ ] **Step 3: Wire the hook into `install_hooks.py`**

Replace the constants block (`hooks/install_hooks.py:39-46`):

```python
PRE_MATCHER = "Read|Write|Edit|NotebookEdit|Glob|Grep|Bash|WebFetch|WebSearch"
POST_MATCHER = "Write|Edit|NotebookEdit|Bash"
# Portable, per-machine: the shell running the hook expands $CLAUDE_ENV_HOME.
# Quoted so a home dir with spaces survives. Keep in sync with the legacy
# suffix match in _strip_cmd so old absolute-path installs are also removable.
_PY = '"$CLAUDE_ENV_HOME/venv/bin/python"'
PRE_CMD = f'{_PY} "$CLAUDE_ENV_HOME/hooks/policy_hook.py"'
POST_CMD = f'{_PY} "$CLAUDE_ENV_HOME/hooks/audit_hook.py"'
```

with:

```python
PRE_MATCHER = "Read|Write|Edit|NotebookEdit|Glob|Grep|Bash|WebFetch|WebSearch"
POST_MATCHER = "Write|Edit|NotebookEdit|Bash"
SESSION_MATCHER = "*"  # every SessionStart source / SessionEnd reason
# Portable, per-machine: the shell running the hook expands $CLAUDE_ENV_HOME.
# Quoted so a home dir with spaces survives. Keep in sync with the legacy
# suffix match in _strip_cmd so old absolute-path installs are also removable.
_PY = '"$CLAUDE_ENV_HOME/venv/bin/python"'
PRE_CMD = f'{_PY} "$CLAUDE_ENV_HOME/hooks/policy_hook.py"'
POST_CMD = f'{_PY} "$CLAUDE_ENV_HOME/hooks/audit_hook.py"'
SESSION_CMD = f'{_PY} "$CLAUDE_ENV_HOME/hooks/session_metrics_hook.py"'
```

Replace the install/uninstall block (`hooks/install_hooks.py:111-124`):

```python
    hooks = settings.setdefault("hooks", {})
    pre = hooks.setdefault("PreToolUse", [])
    post = hooks.setdefault("PostToolUse", [])

    if args.uninstall:
        hooks["PreToolUse"] = _strip_cmd(pre, "hooks/policy_hook.py")
        hooks["PostToolUse"] = _strip_cmd(post, "hooks/audit_hook.py")
        action = "removed from"
    else:
        if not _has_cmd(pre, PRE_CMD):
            pre.append(_entry(PRE_MATCHER, PRE_CMD))
        if not _has_cmd(post, POST_CMD):
            post.append(_entry(POST_MATCHER, POST_CMD))
        action = "installed into"
```

with:

```python
    hooks = settings.setdefault("hooks", {})
    pre = hooks.setdefault("PreToolUse", [])
    post = hooks.setdefault("PostToolUse", [])
    sess_start = hooks.setdefault("SessionStart", [])
    sess_end = hooks.setdefault("SessionEnd", [])

    if args.uninstall:
        hooks["PreToolUse"] = _strip_cmd(pre, "hooks/policy_hook.py")
        hooks["PostToolUse"] = _strip_cmd(post, "hooks/audit_hook.py")
        hooks["SessionStart"] = _strip_cmd(sess_start, "hooks/session_metrics_hook.py")
        hooks["SessionEnd"] = _strip_cmd(sess_end, "hooks/session_metrics_hook.py")
        action = "removed from"
    else:
        if not _has_cmd(pre, PRE_CMD):
            pre.append(_entry(PRE_MATCHER, PRE_CMD))
        if not _has_cmd(post, POST_CMD):
            post.append(_entry(POST_MATCHER, POST_CMD))
        if not _has_cmd(sess_start, SESSION_CMD):
            sess_start.append(_entry(SESSION_MATCHER, SESSION_CMD))
        if not _has_cmd(sess_end, SESSION_CMD):
            sess_end.append(_entry(SESSION_MATCHER, SESSION_CMD))
        action = "installed into"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_install_hooks.py -v`
Expected: all pass, including the 3 new tests.

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add hooks/install_hooks.py tests/test_install_hooks.py
git commit -m "$(cat <<'EOF'
feat(hooks): register session_metrics_hook on SessionStart/SessionEnd

Wires the new cost-tracking hook into the same install/uninstall path as
policy_hook and audit_hook, so `claude-env hooks` picks it up automatically
with no separate opt-in.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `observability/budgets.py` — drop the notification

**Files:**
- Modify: `observability/budgets.py` (remove `import subprocess`, `_notify()`, `--no-notify` flag, and the call site)
- Test: Create `tests/test_budgets.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing new (this task only removes surface area). `observability.budgets.evaluate()` and `load_config()` keep their existing signatures/return shapes unchanged.

- [ ] **Step 1: Write the failing test**

Create `tests/test_budgets.py`:

```python
"""Coverage for observability/budgets.py after removing the macOS
notification. Run: pytest tests/ -q
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


def test_notify_function_removed():
    import observability.budgets as budgets
    importlib.reload(budgets)
    assert not hasattr(budgets, "_notify")


def test_no_notify_flag_removed(monkeypatch, capsys):
    _fresh_db()
    import observability.budgets as budgets
    importlib.reload(budgets)
    monkeypatch.setattr(sys, "argv", ["budgets.py", "--no-notify"])
    with __import__("pytest").raises(SystemExit) as exc:
        budgets.main()
    assert exc.value.code == 2  # argparse: unrecognized argument


def test_evaluate_still_reports_spend(monkeypatch):
    db = _fresh_db()
    import observability.budgets as budgets
    importlib.reload(budgets)
    from observability.collectors import start_session, set_usage_totals
    start_session("s1", "payments")
    set_usage_totals("s1", 1_000_000, 0)  # $3.00 at default pricing
    result = budgets.evaluate()
    row = next(r for r in result["repos"] if r["repo"] == "payments")
    assert row["spent_usd"] == 3.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_budgets.py -v`
Expected: FAIL on `test_notify_function_removed` (`_notify` still exists) and `test_no_notify_flag_removed` (flag still recognized, exit code 0 not 2).

- [ ] **Step 3: Remove the notification**

In `observability/budgets.py`, remove the `import subprocess` line from the imports block, delete the entire `_notify()` function (currently between `evaluate()` and `main()`), remove the `ap.add_argument("--no-notify", action="store_true")` line, and remove the final block in `main()`:

```python
    if result["overall"] != "ok" and not args.no_notify:
        bad = [r["repo"] for r in result["repos"]
               if r["status"] in ("warning", "EXCEEDED")]
        _notify("claude-env budget",
                f"{result['overall']}: {', '.join(bad[:3])}")
    return 1 if result["overall"] == "EXCEEDED" else 0
```

replaced with just:

```python
    return 1 if result["overall"] == "EXCEEDED" else 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_budgets.py -v`
Expected: 3 passed.

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add observability/budgets.py tests/test_budgets.py
git commit -m "$(cat <<'EOF'
refactor(observability): drop budgets.py's macOS notification

The osascript notification was unreliable (notification permissions/DND)
and unnecessary now that cost data populates itself and is visible in the
dashboard/claude-env budget output without a human needing to be pinged.
Budget caps (warn_at, per-repo USD limits) are unchanged -- still optional,
0/absent still means unlimited.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: `observability/dashboard.py` — per-repo cost total

**Files:**
- Modify: `observability/dashboard.py` (insert a new section into `summary()`, right after the existing "session cost (top 10)" block)
- Test: Create `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `metrics_sessions` table (existing schema; now populated by Task 2/3's hook in real use).
- Produces: nothing new for later tasks — this is the last code task.

- [ ] **Step 1: Write the failing test**

Create `tests/test_dashboard.py`:

```python
"""Coverage for the new per-repo cost total section in
observability/dashboard.py::summary(). Run: pytest tests/ -q
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


def test_summary_prints_cost_by_repo_total(capsys):
    db = _fresh_db()
    from observability.collectors import start_session, set_usage_totals
    start_session("s1", "payments")
    set_usage_totals("s1", 1_000_000, 0)   # $3.00
    start_session("s2", "payments")
    set_usage_totals("s2", 1_000_000, 0)   # $3.00 -- same repo, two sessions
    start_session("s3", "web-app")
    set_usage_totals("s3", 2_000_000, 0)   # $6.00

    import observability.dashboard as dashboard
    importlib.reload(dashboard)
    assert dashboard.summary("30d") == 0

    out = capsys.readouterr().out
    assert "cost by repo (total" in out
    payments_line = next(l for l in out.splitlines() if "payments" in l and "sessions=" in l)
    assert "sessions=   2" in payments_line or "sessions=2" in payments_line.replace(" ", "")
    assert "$6.0" in payments_line
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_dashboard.py -v`
Expected: FAIL — `"cost by repo (total" not in out`.

- [ ] **Step 3: Add the section**

In `observability/dashboard.py`, immediately after this existing block (inside `summary()`):

```python
    print("-- session cost (top 10) --")
    rows = db.query(
        f"SELECT session_id, repo, input_tokens, output_tokens, "
        f"ROUND(est_cost_usd,4) AS usd FROM metrics_sessions "
        f"WHERE started_at >= {since} ORDER BY est_cost_usd DESC LIMIT 10")
    if not rows:
        print("   (no sessions)")
    for r in rows:
        print(f"   {r['session_id'][:18]:18s} {str(r['repo'])[:14]:14s} "
              f"in={r['input_tokens']:>8} out={r['output_tokens']:>8} ${r['usd']}")
```

insert:

```python
    print("\n-- cost by repo (total, window) --")
    repo_totals = db.query(
        f"SELECT COALESCE(repo,'(none)') AS repo, COUNT(*) AS sessions, "
        f"ROUND(SUM(est_cost_usd),4) AS usd FROM metrics_sessions "
        f"WHERE started_at >= {since} GROUP BY repo ORDER BY usd DESC")
    if not repo_totals:
        print("   (no sessions)")
    for r in repo_totals:
        print(f"   {str(r['repo'])[:24]:24s} sessions={r['sessions']:>4} ${r['usd']}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_dashboard.py -v`
Expected: 1 passed.

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add observability/dashboard.py tests/test_dashboard.py
git commit -m "$(cat <<'EOF'
feat(observability): add per-repo cost total to dashboard summary

Complements the existing per-session top-10 table with a GROUP BY repo
total, so the aggregated per-repo spend is visible in the dashboard
directly instead of requiring a separate `claude-env budget` run.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Docs — bring the guide and config comments up to date

**Files:**
- Modify: `docs/guide/observability-budgets.md`
- Modify: `config/budgets.yaml`
- Modify: `README.md` (only if it references `--no-notify` or the old manual-setup framing)

**Interfaces:**
- Consumes: nothing (docs only).
- Produces: nothing (terminal task).

- [ ] **Step 1: Check for stale references**

Run: `grep -n "no-notify\|notify\|session ingestion" ./README.md`

If any lines are returned, note the line numbers — they'll need the same treatment as the guide doc below (remove the notification mention; if a line claims cost data requires session ingestion to be scheduled, correct it to describe the automatic hook instead).

- [ ] **Step 2: Rewrite `docs/guide/observability-budgets.md`**

Make these edits to the file (it's read in full at the start of this doc's section headers, so match against the existing text found there):

1. In the **"What each module does"** bullet for `budgets.py`, replace:
   > `budgets.py`" — sums `metrics_sessions.est_cost_usd` per repo for the calendar month to date, compares it against a configured USD cap, and prints a status table (`ok` / `warning` / `EXCEEDED` / `unlimited`). It is advisory: exceeding a budget never blocks an agent from running, it only exits non-zero and fires a best-effort macOS notification, so a CI job or shell prompt can act on it.

   with:
   > `budgets.py` — sums `metrics_sessions.est_cost_usd` per repo for the calendar month to date, compares it against a configured USD cap, and prints a status table (`ok` / `warning` / `EXCEEDED` / `unlimited`). It is advisory: exceeding a budget never blocks an agent from running, it only exits non-zero, so a CI job or shell prompt can act on it. Cost data populates itself with zero configuration — `hooks/session_metrics_hook.py` records it automatically on every session start and end (see "How cost data gets populated" below).

2. Replace the entire **"How `budgets.py` works" → "Cost tracking mechanism"** subsection (which currently states there is no writer and cost data never gets populated) with:

   ```markdown
   ### How cost data gets populated

   `budgets.py` never computes cost itself — it only *reads* `est_cost_usd`.
   That column is written by `hooks/session_metrics_hook.py`, a native
   `SessionStart`/`SessionEnd` hook installed by the same `claude-env hooks`
   step that installs `policy_hook.py`/`audit_hook.py` — no separate
   scheduling, no cron job, no config:

   ```python
   # hooks/session_metrics_hook.py
   if event == "SessionStart":
       start_session(session, _repo_slug(cwd))
   elif event == "SessionEnd":
       start_session(session, _repo_slug(cwd))  # ensure a row exists either way
       total_in, total_out = _sum_transcript_usage(payload.get("transcript_path", ""))
       set_usage_totals(session, total_in, total_out)
       end_session(session)
   ```

   `SessionStart` opens a row keyed by `session_id`/`repo`; `SessionEnd` sums
   `usage.input_tokens`/`output_tokens` across every assistant message in
   that session's own transcript JSONL and **overwrites** (not increments)
   the row's totals via `observability.collectors.set_usage_totals()` — safe
   if the hook ever fires more than once for the same session. This is
   unrelated to `memory/session_ingestor.py`'s nightly job, which populates
   the memory graph and retrieval-feedback signals, not cost.
   ```

3. Delete the **"On `warning` or `EXCEEDED`... fires a macOS notification"** bullet under "Budget enforcement is a soft cap, not a hard block" and its accompanying code reference — replace with a note that only the exit code changes on `EXCEEDED` (matching the code after Task 4).

4. In **"How to use it"**, remove the `--no-notify` example line and its explanatory sentence.

5. In **"Facts, invariants & edge cases"**, remove any bullet describing the notification (there should be none left to describe after Task 4).

- [ ] **Step 3: Update `config/budgets.yaml` comments**

Change the header comment from:

```yaml
# `claude-env budget` prints the status table; exit code 1 when any budget is
# exceeded (usable in CI / shell prompts). warn_at is the fraction of budget
# at which a warning is emitted (and a macOS notification, when available).
```

to:

```yaml
# `claude-env budget` prints the status table; exit code 1 when any budget is
# exceeded (usable in CI / shell prompts). warn_at is the fraction of budget
# at which a warning is emitted. Cost data populates itself automatically
# (hooks/session_metrics_hook.py) -- nothing else to configure here besides
# the cap itself.
```

- [ ] **Step 4: Fix any README references found in Step 1**

Apply the same removal/correction to each line `grep` returned in Step 1, following the same before/after pattern as Step 2.

- [ ] **Step 5: Verify no stale references remain**

Run: `grep -rn "no-notify\|osascript" . --include="*.py" --include="*.md" --include="*.yaml"`
Expected: no output.

- [ ] **Step 6: Run the full suite one more time**

Run: `python3 -m pytest tests/ -q`
Expected: all pass (docs changes don't affect tests, this is a final regression check).

- [ ] **Step 7: Commit**

```bash
git add docs/guide/observability-budgets.md config/budgets.yaml README.md
git commit -m "$(cat <<'EOF'
docs: update budgets/dashboard docs for automatic cost tracking

Replaces the "nothing populates this column" caveat and the macOS
notification write-up with a description of the new SessionStart/SessionEnd
hook, matching the code changes in the prior four commits.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Deploy to `$CLAUDE_ENV_HOME` and do an end-to-end check

**Files:** none (deploy + manual verification only, per `CLAUDE.md` golden rule 2 — editing the repo alone does not change live hook behavior).

- [ ] **Step 1: Copy every changed file to the deployed mirror**

```bash
cp -v ./observability/collectors.py \
      ~/.claude-env/observability/collectors.py
cp -v ./observability/budgets.py \
      ~/.claude-env/observability/budgets.py
cp -v ./observability/dashboard.py \
      ~/.claude-env/observability/dashboard.py
cp -v ./hooks/session_metrics_hook.py \
      ~/.claude-env/hooks/session_metrics_hook.py
cp -v ./hooks/install_hooks.py \
      ~/.claude-env/hooks/install_hooks.py
cp -v ./config/budgets.yaml \
      ~/.claude-env/config/budgets.yaml
```

- [ ] **Step 2: Re-run `claude-env hooks` against a real onboarded test repo**

Pick any already-onboarded repo on this machine (one with `<repo>/.claude/repo-policy.yaml`), then:

```bash
python3 ~/.claude-env/hooks/install_hooks.py --repo /path/to/that/onboarded/repo
cat /path/to/that/onboarded/repo/.claude/settings.json
```

Expected: the printed JSON's `hooks` object now has `SessionStart` and `SessionEnd` keys, each containing one entry whose command ends in `hooks/session_metrics_hook.py`.

- [ ] **Step 3: Simulate a SessionStart + SessionEnd against the real deployed DB**

```bash
echo '{"session_id":"deploy-check-1","cwd":"/path/to/that/onboarded/repo","hook_event_name":"SessionStart","source":"startup"}' \
  | ~/.claude-env/venv/bin/python ~/.claude-env/hooks/session_metrics_hook.py
echo '{"session_id":"deploy-check-1","cwd":"/path/to/that/onboarded/repo","transcript_path":"/nonexistent.jsonl","hook_event_name":"SessionEnd","reason":"other"}' \
  | ~/.claude-env/venv/bin/python ~/.claude-env/hooks/session_metrics_hook.py
python3 ~/.claude-env/observability/budgets.py
```

Expected: the budget table now lists a row for that repo's slug with `sessions=1` (or higher, if the repo already had other sessions), confirming the row was created and closed without needing any config. Delete this test row afterward if you don't want it polluting real cost history:

```bash
~/.claude-env/venv/bin/python -c "
import sys; sys.path.insert(0, '$HOME/.claude-env')
from lib.db import get_db
get_db().execute(\"DELETE FROM metrics_sessions WHERE session_id='deploy-check-1'\")
"
```

- [ ] **Step 4: Run the full test suite one last time**

Run: `python3 -m pytest tests/ -q`
Expected: all pass.

No commit for this task — it's a deploy + verification step, not a code change.

---

## Self-Review Notes

- **Spec coverage:** §1 hook (Task 2), §2 collectors (Task 1), §3 install_hooks (Task 3), §4 budgets notification removal (Task 4), §5 dashboard total (Task 5), §6 docs (Task 6), §7 tests (folded into each task's own test file, per Task Right-Sizing). Deploy step (Task 7) covers `CLAUDE.md`'s golden rule 2, which the spec didn't call out explicitly but is a hard platform requirement.
- **Type consistency:** `set_usage_totals(session_id: str, input_tokens: int, output_tokens: int, model: str = "default")` is defined identically in Task 1 and consumed identically in Task 2's hook code and Task 4/5's tests. `_repo_slug(cwd) -> str | None` matches `lib.repo_policy.repo_slug`'s existing signature used by `audit_hook.py`.
- **No placeholders:** every step has literal code, exact file paths, and exact expected command output.
