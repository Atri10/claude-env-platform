"""
End-to-end tests for the audit ledger, compliance report, and session replay.

These exercise the REAL ledger (claudenv/adapters/audit.py on top of a real
SQLite database with the platform schema applied) and the real report/replay
application services — never fakes — so they catch the runtime SQL /
transaction / hash-chain bugs a mock IDatabase cannot, and they prove the four
contracts the audit subsystem must guarantee:

  1. The ledger is append-only and hash-chained: verify_chain() stays green
     after a sequence of real appends, and tampering with a stored record is
     detected (verify_chain() goes red at the tampered event).
  2. Projections (policy_violations, human_approvals, ...) are written in the
     SAME transaction as the event, so an event can never be persisted without
     its projection — and a projection-insert failure rolls the whole event
     back.
  3. `claude-env report` produces real compliance output (markdown/csv) over a
     time window from seeded events; the ledger-integrity verdict is VERIFIED.
  4. `claude-env replay` lists sessions and replays a session_id, reading real
     events back out of the ledger.

`claudenv.cli` is imported lazily inside the CLI tests (via `_run_cli`) so the
ledger / report / replay unit-level suites still collect and run even when the
CLI module's import graph is transiently broken by another agent's in-progress
work.

Run with:  python3 -m pytest claudenv/tests/test_audit_e2e.py -q
"""
from __future__ import annotations

import sqlite3

import pytest
from click.testing import CliRunner

from claudenv._data import sql_dir as _sql_dir
from claudenv.adapters.audit import SqliteAuditLogger
from claudenv.adapters.persistence import SQLiteDatabase
from claudenv.application.audit import ComplianceReportGenerator, SessionReplay
from claudenv.domain.value_objects import EventId, RepoSlug, SessionId, Tier

_SQL_DIR = _sql_dir()


def _apply_schema(db: SQLiteDatabase) -> None:
    for f in sorted(_SQL_DIR.glob("*.sql")):
        db._conn.executescript(f.read_text())


def _run_cli(args: list[str]):
    """Lazily import the CLI module and invoke a command (so a transient
    import-graph break in another agent's file doesn't stop collection of the
    ledger/report/replay tests that don't need the CLI)."""
    from claudenv.cli import cli

    return CliRunner().invoke(cli, args, catch_exceptions=False)


@pytest.fixture()
def db(tmp_path) -> SQLiteDatabase:
    """Real SQLite database with the platform schema applied."""
    database = SQLiteDatabase(f"sqlite:///{tmp_path}/audit.db")
    _apply_schema(database)
    yield database
    database.close()


@pytest.fixture()
def logger(db) -> SqliteAuditLogger:
    return SqliteAuditLogger(
        db=db,
        session_id=SessionId.from_string("sess-e2e"),
        actor="tester",
        repo=RepoSlug.from_string("my/repo"),
        tier=Tier.INTERNAL,
    )


class TestRealLedgerIntegrity:
    def test_chain_stays_green_after_appends(self, logger):
        for i in range(5):
            logger.tool_call(tool=f"t{i}", args={"x": i}, result_kind="ok")
        res = logger.verify_chain()
        assert res.ok
        assert res.broken_at is None
        assert res.total_events == 5

    def test_tampering_is_detected(self, logger):
        logger.tool_call(tool="t0", args={}, result_kind="ok")
        logger.tool_call(tool="t1", args={}, result_kind="ok")
        logger.tool_call(tool="t2", args={}, result_kind="ok")
        assert logger.verify_chain().ok

        # Simulate an attacker mutating a stored record: drop the defense-in-depth
        # guard trigger, then alter the canonical envelope of event #2. The hash
        # chain must catch this even though the guard is gone.
        conn = logger.db._conn
        conn.execute("DROP TRIGGER IF EXISTS audit_events_no_update")
        conn.execute(
            "UPDATE audit_events SET payload_json = payload_json || ' ' WHERE event_id = 2"
        )

        res = logger.verify_chain()
        assert not res.ok
        assert res.broken_at == EventId.from_string("evt-2")

    def test_append_only_blocks_delete_and_update(self, logger):
        logger.tool_call(tool="t0", args={}, result_kind="ok")
        conn = logger.db._conn
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM audit_events WHERE event_id = 1")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE audit_events SET ts = 'x' WHERE event_id = 1")
        # The ledger is untouched after the rejected mutations.
        assert logger.verify_chain().total_events == 1


class TestProjectionSameTransaction:
    def test_projection_written_with_event(self, db, logger):
        logger.policy_violation(path="a.py", rule="no-secret", decision="blocked", tier=2)
        evt = db.query_one(
            "SELECT event_id, event_type FROM audit_events WHERE event_id = 1"
        )
        proj = db.query_one(
            "SELECT event_id, path, rule, decision FROM policy_violations WHERE event_id = 1"
        )
        assert evt["event_type"] == "policy_violation"
        assert proj["path"] == "a.py"
        assert proj["rule"] == "no-secret"
        assert proj["decision"] == "blocked"

    def test_projection_count_matches_event_count(self, db, logger):
        logger.tool_call(tool="Read", args={"file_path": "x"}, result_kind="ok")
        logger.tool_call(tool="Edit", args={"file_path": "y"}, result_kind="ok")
        logger.policy_violation(path="p", rule="r", decision="warn", tier=1)
        n_events = db.query_one("SELECT COUNT(*) AS n FROM audit_events")["n"]
        n_tool = db.query_one("SELECT COUNT(*) AS n FROM tool_calls")["n"]
        n_pv = db.query_one("SELECT COUNT(*) AS n FROM policy_violations")["n"]
        assert n_events == 3
        assert n_tool == 2
        assert n_pv == 1

    def test_projection_failure_rolls_back_event(self, db):
        """A projection-insert failure must abort the whole transaction, so the
        event row is never persisted either."""

        class FailingProjectionLogger(SqliteAuditLogger):
            def _write_projection(self, tx, event_type, event_id_int, ts, payload):
                raise RuntimeError("projection insert boom")

        # Seed one valid event first so we can prove the rollback is surgical.
        good = SqliteAuditLogger(
            db=db, session_id=SessionId.from_string("s-good"), actor="a",
            repo="r", tier=Tier.INTERNAL,
        )
        good.tool_call(tool="ok", args={}, result_kind="ok")
        assert db.query_one("SELECT COUNT(*) AS n FROM audit_events")["n"] == 1

        bad = FailingProjectionLogger(
            db=db, session_id=SessionId.from_string("s-bad"), actor="z",
            repo="r", tier=Tier.INTERNAL,
        )
        with pytest.raises(RuntimeError):
            bad.tool_call(tool="bad", args={}, result_kind="ok")

        # The failed append left no event and no orphan projection.
        assert db.query_one("SELECT COUNT(*) AS n FROM audit_events")["n"] == 1
        assert db.query_one("SELECT COUNT(*) AS n FROM tool_calls")["n"] == 1
        assert _ledger_ok(db)

    def test_human_approval_request_and_resolve_linked(self, db, logger):
        req_id = logger.human_approval_request(agent="agent", action="rm -rf", tier=3)
        logger.human_approval_resolve(request_id=req_id, decision="denied", decided_by="ops")
        row = db.query_one(
            "SELECT request_id, decision, decided_by FROM human_approvals WHERE request_id = ?",
            (req_id,),
        )
        assert row["decision"] == "denied"
        assert row["decided_by"] == "ops"
        # Both the request and the resolution are individual ledger events.
        assert db.query_one("SELECT COUNT(*) AS n FROM audit_events")["n"] == 2


def _ledger_ok(db: SQLiteDatabase) -> bool:
    lg = SqliteAuditLogger(
        db=db, session_id=SessionId.from_string("verify"), actor="v"
    )
    return lg.verify_chain().ok


class TestComplianceReportRealLedger:
    def _seed(self, db) -> None:
        lg = SqliteAuditLogger(
            db=db, session_id=SessionId.from_string("sess-alpha"),
            actor="agent", repo=RepoSlug.from_string("payments"), tier=Tier.RESTRICTED,
        )
        lg.tool_call(tool="Read", args={"file_path": "a.py"}, result_kind="ok")
        lg.policy_violation(path="secret.py", rule="no-secret", decision="blocked", tier=3)
        lg.security_event(
            category="injection", severity="high",
            detail="prompt injection attempt", source="web",
        )
        lg2 = SqliteAuditLogger(
            db=db, session_id=SessionId.from_string("sess-beta"),
            actor="human", repo=RepoSlug.from_string("payments"), tier=Tier.RESTRICTED,
        )
        req = lg2.human_approval_request(agent="agent", action="deploy", tier=3)
        lg2.human_approval_resolve(request_id=req, decision="approved", decided_by="ops")

    def test_markdown_has_real_seeded_content(self, db):
        from claudenv.adapters.persistence.sqlite import SQLiteAuditRepository

        self._seed(db)
        gen = ComplianceReportGenerator(db, SQLiteAuditRepository(db))
        out = gen.generate(window="30d", format="markdown")

        assert "ledger integrity" in out
        assert "VERIFIED" in out
        assert "payments" in out            # repo appears in projections
        assert "no-secret" in out          # seeded policy rule
        assert "prompt injection attempt" in out  # seeded security detail
        assert "deploy" in out             # seeded approval action

    def test_csv_export_has_real_rows(self, db):
        from claudenv.adapters.persistence.sqlite import SQLiteAuditRepository

        self._seed(db)
        gen = ComplianceReportGenerator(db, SQLiteAuditRepository(db))
        out = gen.generate(window="30d", format="csv")

        assert out.startswith("event_id,ts,event_type")
        # Every seeded event is exported as a CSV row.
        assert out.count("\n") >= 5  # header + >=4 events
        assert "sess-alpha" in out
        assert "sess-beta" in out

    def test_chain_broken_reported_in_report(self, db):
        from claudenv.adapters.persistence.sqlite import SQLiteAuditRepository

        self._seed(db)
        # Break the chain after the fact.
        db._conn.execute("DROP TRIGGER IF EXISTS audit_events_no_update")
        db._conn.execute(
            "UPDATE audit_events SET payload_json = payload_json || ' ' WHERE event_id = 1"
        )
        gen = ComplianceReportGenerator(db, SQLiteAuditRepository(db))
        out = gen.generate(window="30d", format="markdown")
        assert "BROKEN" in out


class TestSessionReplayRealLedger:
    def _seed(self, db) -> None:
        lg = SqliteAuditLogger(
            db=db, session_id=SessionId.from_string("sess-alpha"),
            actor="agent", repo=RepoSlug.from_string("payments"), tier=Tier.RESTRICTED,
        )
        lg.tool_call(tool="Read", args={"file_path": "a.py"}, result_kind="ok")
        lg.policy_violation(path="secret.py", rule="no-secret", decision="blocked", tier=3)

    def test_list_recent_returns_seeded_sessions(self, db):
        self._seed(db)
        sessions = SessionReplay(db).list_recent(20)
        assert any(s["session_id"] == "sess-alpha" for s in sessions)
        alpha = next(s for s in sessions if s["session_id"] == "sess-alpha")
        assert alpha["events"] >= 2
        assert "agent" in alpha["actors"]

    def test_get_timeline_reads_real_events(self, db):
        self._seed(db)
        timeline = SessionReplay(db).get_timeline("sess-alpha")
        types = {step["type"] for step in timeline}
        assert "tool_call" in types
        assert "policy_violation" in types
        # The human-readable summary carries the real payload fields.
        summaries = " ".join(step["summary"] for step in timeline)
        assert "a.py" in summaries
        assert "no-secret" in summaries


# ---------------------------------------------------------------------------
# CLI E2E — drive the actual `report` / `replay` command callbacks through
# click's CliRunner against an isolated temp $CLAUDE_ENV_HOME + SQLite DB.
# ---------------------------------------------------------------------------
@pytest.fixture()
def cli_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "state").mkdir(parents=True)
    dsn = f"sqlite:///{home}/state/claude-env.db"
    monkeypatch.setenv("CLAUDE_ENV_HOME", str(home))
    monkeypatch.setenv("CLAUDE_ENV_DSN", dsn)
    monkeypatch.setenv("EMBED_BACKEND", "dummy")
    monkeypatch.setenv("RERANKER_BACKEND", "noop")

    db = SQLiteDatabase(dsn)
    _apply_schema(db)
    db.close()

    import claudenv.di as di

    di.reset_container()
    yield dsn
    di.reset_container()


def _seed_cli(dsn: str) -> None:
    seed_db = SQLiteDatabase(dsn)
    lg = SqliteAuditLogger(
        db=seed_db, session_id=SessionId.from_string("sess-alpha"),
        actor="agent", repo=RepoSlug.from_string("payments"), tier=Tier.RESTRICTED,
    )
    lg.tool_call(tool="Read", args={"file_path": "a.py"}, result_kind="ok")
    lg.policy_violation(path="secret.py", rule="no-secret", decision="blocked", tier=3)
    lg.security_event(
        category="injection", severity="high",
        detail="prompt injection attempt", source="web",
    )
    lg2 = SqliteAuditLogger(
        db=seed_db, session_id=SessionId.from_string("sess-beta"),
        actor="human", repo=RepoSlug.from_string("payments"), tier=Tier.RESTRICTED,
    )
    req = lg2.human_approval_request(agent="agent", action="deploy", tier=3)
    lg2.human_approval_resolve(request_id=req, decision="approved", decided_by="ops")
    seed_db.close()


class TestReportCommandCLI:
    def test_markdown_produces_real_output(self, cli_env):
        _seed_cli(cli_env)
        result = _run_cli(["report", "--window", "30d"])
        assert result.exit_code == 0
        out = result.output
        assert "ledger integrity" in out
        assert "VERIFIED" in out
        assert "payments" in out
        assert "no-secret" in out
        assert "prompt injection attempt" in out

    def test_csv_produces_real_output(self, cli_env):
        _seed_cli(cli_env)
        result = _run_cli(["report", "--window", "30d", "--format", "csv"])
        assert result.exit_code == 0
        assert "event_id,ts,event_type" in result.output

    def test_writes_to_out_file(self, cli_env, tmp_path):
        _seed_cli(cli_env)
        outp = tmp_path / "report.md"
        result = _run_cli(["report", "--window", "30d", "--out", str(outp)])
        assert result.exit_code == 0
        text = outp.read_text()
        assert text.strip()
        assert "VERIFIED" in text


class TestReplayCommandCLI:
    def test_list_shows_seeded_sessions(self, cli_env):
        _seed_cli(cli_env)
        result = _run_cli(["replay", "--list"])
        assert result.exit_code == 0
        assert "sess-alpha" in result.output
        assert "sess-beta" in result.output

    def test_replay_session_reads_real_events(self, cli_env):
        _seed_cli(cli_env)
        result = _run_cli(["replay", "sess-alpha"])
        assert result.exit_code == 0
        out = result.output
        assert "tool_call" in out
        assert "policy_violation" in out
        assert "a.py" in out

    def test_replay_unknown_session_is_empty(self, cli_env):
        _seed_cli(cli_env)
        result = _run_cli(["replay", "does-not-exist"])
        assert result.exit_code == 0
        assert result.output.strip() == ""
