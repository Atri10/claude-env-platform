"""
Tests for the observability feature set (budgets, feedback, dashboard).

Covers three layers:
  - Pure domain logic (budget classification, boost formula, percentile, window).
  - SQLite adapters against a real temp DB with the platform schema applied.
  - Application services composing the two.

Notably includes the boost-formula regression the guide flagged as previously
untested (docs/guide/observability-budgets.md): boost(n) = 0.05 * ln(1+min(n,20)).
"""
from __future__ import annotations

import math

import pytest

from claudenv._data import sql_dir as _sql_dir
from claudenv.adapters.observability import (
    SQLiteFeedbackRepository,
    SQLiteMetricsRepository,
    SQLiteProjectionRepository,
    YamlBudgetConfig,
)
from claudenv.adapters.persistence import SQLiteDatabase
from claudenv.application.observability import (
    BudgetService,
    DashboardService,
    FeedbackService,
)
from claudenv.domain.observability import (
    BOOST_CAP,
    BOOST_UNIT,
    BudgetStatus,
    nearest_rank_percentile,
    parse_window_days,
    query_hash,
    usage_boost,
)
from claudenv.domain.observability.budget import RepoSpend, evaluate_budgets

_SQL_DIR = _sql_dir()


@pytest.fixture()
def db(tmp_path) -> SQLiteDatabase:
    database = SQLiteDatabase(f"sqlite:///{tmp_path}/test.db")
    for f in sorted(_SQL_DIR.glob("*.sql")):
        database._conn.executescript(f.read_text())
    return database


# --------------------------------------------------------------------------
# Domain: budget evaluation
# --------------------------------------------------------------------------
class TestBudgetEvaluator:
    def _spend(self, repo: str, usd: float) -> RepoSpend:
        return RepoSpend(repo=repo, sessions=1, input_tokens=0, output_tokens=0, spent_usd=usd)

    def test_zero_budget_is_unlimited(self):
        ev = evaluate_budgets([self._spend("a", 999.0)], default_budget=0, per_repo_budgets={})
        assert ev.repos[0].status is BudgetStatus.UNLIMITED
        assert ev.repos[0].pct is None
        assert ev.overall is BudgetStatus.OK

    def test_negative_budget_is_unlimited(self):
        ev = evaluate_budgets([self._spend("a", 999.0)], default_budget=-5, per_repo_budgets={})
        assert ev.repos[0].status is BudgetStatus.UNLIMITED

    def test_exceeded_is_inclusive_at_one(self):
        ev = evaluate_budgets([self._spend("a", 100.0)], default_budget=100, per_repo_budgets={})
        assert ev.repos[0].status is BudgetStatus.EXCEEDED
        assert ev.overall is BudgetStatus.EXCEEDED

    def test_warning_is_inclusive_at_warn_at(self):
        ev = evaluate_budgets([self._spend("a", 80.0)], default_budget=100, per_repo_budgets={}, warn_at=0.8)
        assert ev.repos[0].status is BudgetStatus.WARNING

    def test_ok_below_warn_at(self):
        ev = evaluate_budgets([self._spend("a", 50.0)], default_budget=100, per_repo_budgets={}, warn_at=0.8)
        assert ev.repos[0].status is BudgetStatus.OK

    def test_per_repo_override_wins(self):
        ev = evaluate_budgets(
            [self._spend("payments", 120.0)],
            default_budget=1000,
            per_repo_budgets={"payments": 100},
        )
        assert ev.repos[0].status is BudgetStatus.EXCEEDED

    def test_overall_folds_to_worst(self):
        ev = evaluate_budgets(
            [self._spend("a", 10.0), self._spend("b", 200.0)],
            default_budget=100,
            per_repo_budgets={},
        )
        assert ev.overall is BudgetStatus.EXCEEDED


# --------------------------------------------------------------------------
# Domain: boost formula, percentile, window, query hash
# --------------------------------------------------------------------------
class TestDomainMath:
    def test_boost_formula_exact(self):
        assert usage_boost(1) == pytest.approx(BOOST_UNIT * math.log(2))
        assert usage_boost(5) == pytest.approx(BOOST_UNIT * math.log(6))

    def test_boost_caps_at_boost_cap(self):
        assert usage_boost(BOOST_CAP) == usage_boost(BOOST_CAP + 100)
        assert usage_boost(BOOST_CAP) == pytest.approx(BOOST_UNIT * math.log1p(BOOST_CAP))

    def test_boost_zero_is_zero(self):
        assert usage_boost(0) == 0.0

    def test_percentile_nearest_rank(self):
        assert nearest_rank_percentile([], 50) == 0.0
        assert nearest_rank_percentile([10, 20, 30], 50) == 20
        assert nearest_rank_percentile([1, 2, 3, 4], 95) == 4

    def test_window_parsing(self):
        assert parse_window_days("7d") == 7.0
        assert parse_window_days("24h") == 1.0
        assert parse_window_days("") == 30.0
        assert parse_window_days("7w") == 7.0  # no 'w' unit -> days

    def test_query_hash_is_16_hex(self):
        h = query_hash("hello")
        assert len(h) == 16
        assert h == query_hash("hello")
        assert h != query_hash("world")


# --------------------------------------------------------------------------
# Adapters + services against a real DB
# --------------------------------------------------------------------------
class TestBudgetServiceIntegration:
    def test_evaluate_over_real_metrics(self, db, tmp_path):
        db.execute(
            "INSERT INTO metrics_sessions (session_id, repo, started_at, input_tokens, output_tokens, est_cost_usd) "
            "VALUES (?,?,?,?,?,?)",
            ("s1", "payments", "2999-01-15T00:00:00", 100, 50, 120.0),
        )
        cfg_path = tmp_path / "budgets.yaml"
        cfg_path.write_text("warn_at: 0.8\nmonthly_usd:\n  default: 0\n  repos:\n    payments: 100\n")
        svc = BudgetService(SQLiteMetricsRepository(db), YamlBudgetConfig(cfg_path))
        ev = svc.evaluate()
        payments = [r for r in ev.repos if r.repo == "payments"][0]
        assert payments.status is BudgetStatus.EXCEEDED
        assert svc.is_exceeded() is True


class TestFeedbackServiceIntegration:
    def test_record_and_boost_roundtrip(self, db):
        svc = FeedbackService(SQLiteFeedbackRepository(db))
        svc.record_retrieved(
            "repo", "main", "some query",
            [{"chunk_id": "c1", "file_path": "a.py"}, {"chunk_id": "c2", "file_path": "b.py"}],
            "sess",
        )
        totals = svc.stats("repo")
        assert totals.get("retrieved") == 2
        # No 'used' signals yet -> empty boosts.
        assert svc.usage_boosts("repo", "main") == {}
        # Insert a 'used' signal and confirm the boost appears.
        db.execute(
            "INSERT INTO rag_chunk_feedback (ts,repo,branch,chunk_id,file_path,query_hash,signal,session_id) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("2020-01-01T00:00:00", "repo", "main", "c1", "a.py", "qh", "used", "sess"),
        )
        boosts = svc.usage_boosts("repo", "main")
        assert boosts["c1"] == pytest.approx(usage_boost(1))

    def test_record_retrieved_skips_chunks_without_id(self, db):
        svc = FeedbackService(SQLiteFeedbackRepository(db))
        svc.record_retrieved("repo", "main", "q", [{"file_path": "a.py"}], "sess")
        assert svc.stats("repo").get("retrieved") is None

    def test_boost_disabled_by_env(self, db, monkeypatch):
        monkeypatch.setenv("CLAUDE_ENV_FEEDBACK_BOOST", "false")
        db.execute(
            "INSERT INTO rag_chunk_feedback (ts,repo,branch,chunk_id,file_path,query_hash,signal,session_id) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("2020-01-01T00:00:00", "repo", "main", "c1", "a.py", "qh", "used", "sess"),
        )
        svc = FeedbackService(SQLiteFeedbackRepository(db))
        assert svc.usage_boosts("repo", "main") == {}


class TestDashboardServiceIntegration:
    def test_summary_shape(self, db):
        db.execute(
            "INSERT INTO metrics_sessions (session_id, repo, started_at, input_tokens, output_tokens, est_cost_usd) "
            "VALUES (?,?,?,?,?,?)",
            ("s1", "repo", "2999-01-15T00:00:00", 10, 5, 1.0),
        )
        db.execute(
            "INSERT INTO metrics_latency (ts, component, operation, duration_ms, repo) VALUES (?,?,?,?,?)",
            ("2999-01-15T00:00:00", "rag.retrieve", "search", 42, "repo"),
        )
        svc = DashboardService(SQLiteMetricsRepository(db), SQLiteProjectionRepository(db))
        summary = svc.summary("30d")
        assert set(summary) >= {
            "top_session_costs", "latency", "retrieval_quality",
            "policy_violations", "security_events", "open_approvals",
        }
        assert "rag.retrieve" in summary["latency"]
        assert summary["latency"]["rag.retrieve"]["count"] == 1
