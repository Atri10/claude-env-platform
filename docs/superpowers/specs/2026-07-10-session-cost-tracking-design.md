# Session cost tracking — design

Date: 2026-07-10
Status: approved

## Problem

`observability/budgets.py` and the "session cost" section of
`observability/dashboard.py` both read `metrics_sessions.est_cost_usd`. In
practice this column is always empty: `observability/collectors.py` defines
`start_session()` / `record_tokens()` / `end_session()` to write it, but
nothing in the codebase calls them outside a test fixture. There is no
`SessionStart`/`SessionEnd` hook registered anywhere — `hooks/install_hooks.py`
only wires `PreToolUse`/`PostToolUse`. Result: `claude-env budget` always
prints "no session spend recorded," and the dashboard's cost table is always
blank, regardless of actual usage.

Separately, `budgets.py`'s macOS notification (`osascript -e 'display
notification ...'`) is a nicety nobody asked for and isn't reliable across
machines (notification permissions/Do Not Disturb), and the feature overall
depends on `memory/session_ingestor.py`'s nightly job — which most users never
schedule (no macOS/launchd auto-setup) and which doesn't even populate cost
data in the first place.

## Goal

Cost data should populate itself with zero user configuration: recorded
automatically each time a session starts/resumes and each time it ends, and
visible per-session-per-repo the moment someone opens the dashboard or runs
`claude-env budget` — no cron job, no notification, no separate setup step.

## Design

### 1. New hook: `hooks/session_metrics_hook.py`

One file, dispatching on `payload["hook_event_name"]`, following the same
shape as `hooks/audit_hook.py` (stdin JSON in, broad `try/except: pass`,
never blocks or fails the session):

- **`SessionStart`** (payload carries `source`: `startup`/`resume`/`clear`/
  `compact` — confirmed via Claude Code's hooks docs; this hook uses matcher
  `"*"` and treats all sources the same): resolve `repo` via
  `lib.repo_policy.repo_slug(cwd, fallback_to_basename=True)` (same helper
  `audit_hook.py` already uses), then call
  `observability.collectors.start_session(session_id, repo)`. This is already
  `INSERT OR IGNORE`, so a resumed session's existing row is left alone.
- **`SessionEnd`** (payload carries `reason`: `clear`/`resume`/`logout`/
  `prompt_input_exit`/`bypass_permissions_disabled`/`other`; matcher `"*"`
  here too — note that a session that merely *pauses* to be resumed later does
  **not** fire `SessionEnd`, only an actual close does, so cost is captured at
  real session boundaries, not on every pause): read the transcript file at
  `payload["transcript_path"]` (the same JSONL format `memory/
  session_ingestor.py::_parse_transcript` already reads), sum
  `message.usage.input_tokens` and `message.usage.output_tokens` across every
  record with `message.role == "assistant"`, then call
  `observability.collectors.set_usage_totals(session_id, total_input,
  total_output)` followed by `collectors.end_session(session_id)`.
  Missing/malformed lines are skipped (mirrors `_parse_transcript`'s
  `try/except` per-line pattern); a missing `usage` field on a message counts
  as 0 for that message, never raises.

### 2. `observability/collectors.py`: one new function

```python
def set_usage_totals(session_id: str, input_tokens: int, output_tokens: int,
                     model: str = "default") -> None:
    """Overwrite (not increment) a session's usage/cost totals. Called once
    per SessionEnd with the transcript's full cumulative usage, so this must
    be idempotent if the hook ever fires more than once for the same session."""
    pin, pout = PRICES.get(model, PRICES["default"])
    cost = (input_tokens * pin + output_tokens * pout) / 1_000_000
    get_db().execute(
        "UPDATE metrics_sessions SET input_tokens=?, output_tokens=?, "
        "est_cost_usd=? WHERE session_id=?",
        (input_tokens, output_tokens, cost, session_id))
```

`record_tokens()` (the existing incremental add) becomes dead code once this
ships and is deleted — nothing needs incremental updates now that the whole
transcript is summed at `SessionEnd`. `start_session()` and `end_session()`
are reused as-is.

### 3. `hooks/install_hooks.py`: wire the new hook

Add a `SESSION_CMD` pointing at `session_metrics_hook.py` (same portable
`"$CLAUDE_ENV_HOME/venv/bin/python" "$CLAUDE_ENV_HOME/hooks/
session_metrics_hook.py"` form as the existing commands) and register it
under both `hooks["SessionStart"]` and `hooks["SessionEnd"]` with matcher
`"*"` (both event types accept a matcher on `source`/`reason`; `"*"` catches
every start reason and every end reason). Extend `_strip_cmd`'s uninstall pass
to also clean up `SessionStart`/`SessionEnd` entries for this script. This is
part of the same `claude-env hooks` install/uninstall command — no new CLI
verb, no separate opt-in.

### 4. `observability/budgets.py`: remove the notification

Delete `_notify()` entirely, the `--no-notify` CLI flag, and the call site in
`main()` that fires it on `warning`/`EXCEEDED`. `evaluate()`, `load_config()`,
and the cap/`warn_at` threshold logic are unchanged — budgets stay an
*optional* per-repo USD cap (0/absent = unlimited, unchanged from today), just
without the desktop notification.

### 5. `observability/dashboard.py`: add a per-repo total

The existing "session cost (top 10)" table already includes a `repo` column
per session — once populated by the new hook, that already answers "cost per
session, with repo visible." Add one more section, `-- cost by repo (total,
window) --`, immediately after it:

```sql
SELECT COALESCE(repo,'(none)') AS repo, COUNT(*) AS sessions,
       ROUND(SUM(est_cost_usd),4) AS usd
FROM metrics_sessions WHERE started_at >= {since}
GROUP BY repo ORDER BY usd DESC
```

This gives the aggregated per-repo view directly in the dashboard, without
needing to separately run `claude-env budget`.

### 6. Docs

- `docs/guide/observability-budgets.md`: rewrite the "Cost tracking
  mechanism" section (currently states there is no writer — now describes
  `session_metrics_hook.py`), remove the "macOS notification" subsection and
  its references in "How to use it" / "Facts, invariants & edge cases", update
  the config reference table's `--no-notify` row (removed), and add the new
  dashboard section to the `dashboard.py` write-up.
- `config/budgets.yaml`: update the header comment — it currently doesn't
  mention notifications directly but references "advisory" framing; adjust
  wording that implies manual setup is needed for cost data to appear.
- `README.md`: check for any `--no-notify` / budget-notification mentions and
  remove.

### 7. Tests

New `tests/test_session_metrics_hook.py`:
- `SessionStart` creates a `metrics_sessions` row with the resolved repo.
- `SessionStart` on an existing session_id doesn't clobber `started_at`
  (INSERT OR IGNORE behavior, already covered by reusing `start_session`, but
  test it through the new hook's entry point).
- `SessionEnd` against a fixture transcript (a small JSONL with 2-3 assistant
  messages carrying `usage.input_tokens`/`output_tokens`) sets the expected
  totals and `est_cost_usd`, and sets `ended_at`.
- `SessionEnd` run twice on the same transcript produces the same totals
  (idempotency).
- A malformed transcript line, or a message with no `usage` field, doesn't
  crash the hook (exit 0, no exception escapes `main()`).
- `install_hooks.py`: `SessionStart`/`SessionEnd` entries are present after
  install, absent after `--uninstall`.

Update/remove: any existing test referencing `budgets.py --no-notify` or
`_notify` (none currently exist per `docs/guide/observability-budgets.md`'s
"Test coverage" section, which states there is no `tests/test_budgets.py`
today — this spec doesn't change that gap, it's out of scope beyond the new
hook file).

## Out of scope

- Adding a `tests/test_budgets.py` / `tests/test_dashboard.py` covering
  pre-existing behavior unrelated to this change (cap thresholds, Datasette
  `serve` mode, latency/retrieval-quality sections) — not touched by this
  design.
- Per-model pricing beyond the existing single `"default"` entry in `PRICES`
  — unchanged, still a config-free placeholder table in code (this is a
  pre-existing simplification, not something this design set out to fix).
- Changing how `memory/session_ingestor.py` or its nightly scheduling work —
  they remain independent of cost tracking after this change (the memory
  graph / retrieval-feedback purposes are unaffected).
