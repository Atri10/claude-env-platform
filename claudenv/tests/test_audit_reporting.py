"""
Tests for claudenv application audit reporting — ComplianceReportGenerator
and SessionReplay. This module didn't exist after the refactor (cli.py's
report/replay commands imported from it, but it was never restored); these
tests cover the ported-and-redesigned implementation end to end against a
fake IDatabase/IAuditRepository, matching the read shapes cli.py expects.
"""
from __future__ import annotations

import json

import pytest

from claudenv.application.audit import (
    ComplianceReportGenerator, SessionReplay, _parse_window,
)
from claudenv.domain.audit import ChainVerificationResult
from claudenv.domain.value_objects import EventId


class FakeDatabase:
    """Records every query so tests can assert on SQL shape without a real DB."""

    def __init__(self, table_data: dict[str, list[dict]]):
        self._table_data = table_data
        self.queries: list[tuple[str, tuple]] = []

    def query(self, sql: str, params: tuple = ()):
        self.queries.append((sql, params))
        for table, rows in self._table_data.items():
            if f"FROM {table}" in sql:
                return rows
        return []

    def query_one(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None


class FakeAuditRepository:
    def __init__(self, result: ChainVerificationResult):
        self._result = result

    def verify_chain(self):
        return self._result


class TestParseWindow:
    def test_hours(self):
        from datetime import datetime, timezone
        result = _parse_window("24h")
        delta = datetime.now(timezone.utc) - result
        assert 23.9 <= delta.total_seconds() / 3600 <= 24.1

    def test_days(self):
        from datetime import datetime, timezone
        result = _parse_window("7d")
        delta = datetime.now(timezone.utc) - result
        assert 6.9 <= delta.total_seconds() / 86400 <= 7.1

    def test_weeks(self):
        from datetime import datetime, timezone
        result = _parse_window("2w")
        delta = datetime.now(timezone.utc) - result
        assert 13.9 <= delta.total_seconds() / 86400 <= 14.1

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError):
            _parse_window("bogus")


class TestComplianceReportGenerator:
    def _generator(self, verified=True, broken_at=None, total_events=5, extra_tables=None):
        tables = {
            "audit_events": [{"event_type": "tool_call", "n": 3}],
            "tool_calls": [],
            "policy_violations": [],
            "security_events": [],
            "human_approvals": [],
            "metrics_sessions": [],
            **(extra_tables or {}),
        }
        db = FakeDatabase(tables)
        audit_repo = FakeAuditRepository(
            ChainVerificationResult(ok=verified, broken_at=broken_at, total_events=total_events)
        )
        return ComplianceReportGenerator(db, audit_repo), db

    def test_gather_includes_chain_verification(self):
        gen, _ = self._generator(verified=True, total_events=42)
        data = gen.gather(window="7d")
        assert data["chain"]["verified"] is True
        assert data["chain"]["total_events"] == 42

    def test_gather_reflects_broken_chain(self):
        broken_id = EventId.from_string("evt-13")
        gen, _ = self._generator(verified=False, broken_at=broken_id, total_events=12)
        data = gen.gather(window="30d")
        assert data["chain"]["verified"] is False
        assert data["chain"]["first_broken_event"] == broken_id

    def test_gather_defaults_repo_to_all(self):
        gen, _ = self._generator()
        data = gen.gather(window="30d", repo=None)
        assert data["repo"] == "(all)"

    def test_gather_with_repo_filter_passes_repo_param(self):
        gen, db = self._generator()
        gen.gather(window="30d", repo="my/repo")
        # every query should have been given the repo as a trailing param
        # wherever a repo filter clause was included
        assert any("my/repo" in params for _, params in db.queries)

    def test_generate_markdown_contains_integrity_line(self):
        gen, _ = self._generator(verified=True)
        report = gen.generate(window="7d", format="markdown")
        assert "claude-env compliance report" in report
        assert "VERIFIED" in report

    def test_generate_markdown_flags_broken_chain(self):
        broken_id = EventId.from_string("evt-7")
        gen, _ = self._generator(verified=False, broken_at=broken_id)
        report = gen.generate(window="7d", format="markdown")
        assert "BROKEN at event evt-7" in report

    def test_generate_json_round_trips(self):
        gen, _ = self._generator(verified=True, total_events=9)
        report = gen.generate(window="7d", format="json")
        data = json.loads(report)
        assert data["chain"]["total_events"] == 9

    def test_generate_csv_returns_string(self):
        gen, _ = self._generator()
        report = gen.generate(window="7d", format="csv")
        assert isinstance(report, str)

    def test_generate_unknown_format_raises(self):
        gen, _ = self._generator()
        with pytest.raises(ValueError):
            gen.generate(window="7d", format="xml")


class TestSessionReplay:
    def test_list_recent_shape_matches_cli_expectations(self):
        db = FakeDatabase({
            "audit_events": [
                {"session_id": "sess-1", "events": 12, "first": "t0", "last": "t1", "actors": "agent"},
            ],
        })
        replay = SessionReplay(db)
        sessions = replay.list_recent(20)
        assert sessions[0]["session_id"] == "sess-1"
        assert sessions[0]["events"] == 12
        assert sessions[0]["first"] == "t0"
        assert sessions[0]["last"] == "t1"
        assert sessions[0]["actors"] == "agent"

    def test_get_timeline_summarizes_tool_call(self):
        payload = json.dumps({"body": {"tool": "filesystem.read", "args": {"path": "a.py"}, "result_kind": "ok"}})
        db = FakeDatabase({
            "audit_events": [
                {"event_id": 1, "ts": "t0", "event_type": "tool_call", "actor": "agent",
                 "repo": "my/repo", "payload_json": payload},
            ],
        })
        replay = SessionReplay(db)
        timeline = replay.get_timeline("sess-1")
        assert len(timeline) == 1
        assert timeline[0]["type"] == "tool_call"
        assert "filesystem.read" in timeline[0]["summary"]
        assert "a.py" in timeline[0]["summary"]

    def test_get_timeline_handles_malformed_payload_gracefully(self):
        db = FakeDatabase({
            "audit_events": [
                {"event_id": 1, "ts": "t0", "event_type": "tool_call", "actor": "agent",
                 "repo": None, "payload_json": "not valid json"},
            ],
        })
        replay = SessionReplay(db)
        timeline = replay.get_timeline("sess-1")
        assert len(timeline) == 1
        assert timeline[0]["body"] == {}

    def test_get_timeline_empty_for_unknown_session(self):
        db = FakeDatabase({"audit_events": []})
        replay = SessionReplay(db)
        assert replay.get_timeline("no-such-session") == []
