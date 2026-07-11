"""Coverage for Indexer._validate_embedder_dim (rag/indexers/indexer.py). Run: pytest tests/ -q

Regression context: the original version of this method (a) hardcoded the branch
as "master" instead of using the repo's actual current branch, and (b) called
LanceStore.open() to check the schema -- which CREATES the table if missing,
using the *current* embedder's own dim, making the check a structural no-op that
also silently left behind a bogus empty table for a branch that was never
indexed. On top of that, the deliberate ValueError it raised on a real mismatch
was caught by its own broad `except Exception`, so it never actually stopped
indexing. This file exercises the fixed version: read-only (no table_names()
mutation), keyed by the real branch, and mismatches actually propagate.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from rag.indexers.indexer import Indexer


class _FakeTable:
    def __init__(self, dim: int):
        self._dim = dim

    @property
    def schema(self):
        class _Field:
            def __init__(self, name, list_size=None):
                self.name = name
                self.type = type("T", (), {"list_size": list_size})()
        return [_Field("chunk_id"), _Field("vector", self._dim), _Field("text")]


class _FakeConnection:
    """Stands in for LanceStore.db (a lancedb.connect() object)."""
    def __init__(self, tables: dict[str, int]):
        self._tables = tables   # table_name -> dim

    def table_names(self):
        return list(self._tables)

    def open_table(self, name):
        return _FakeTable(self._tables[name])


class _FakeStore:
    def __init__(self, tables: dict[str, int]):
        self.db = _FakeConnection(tables)


class _FakeEmbedder:
    def __init__(self, dim: int, model_name: str = "fake-model"):
        self.dim = dim
        self.model_name = model_name


def _make_indexer(tmp_path, repo="demo-repo"):
    """A minimal Indexer whose __init__ side effects (PolicyEngine.load, git,
    AuditLogger/DB) aren't needed for testing _validate_embedder_dim in
    isolation -- bypass __init__ and set only what the method reads."""
    idx = Indexer.__new__(Indexer)
    idx.repo = repo

    class _StubAudit:
        def security_event(self, *a, **k):
            pass
    idx.audit = _StubAudit()
    return idx


def test_no_existing_table_does_not_raise(tmp_path):
    idx = _make_indexer(tmp_path)
    idx.embedder = _FakeEmbedder(dim=4096)
    idx.store = _FakeStore(tables={})
    idx._validate_embedder_dim("main")   # nothing indexed yet -- no conflict possible


def test_matching_dim_on_actual_branch_does_not_raise(tmp_path):
    idx = _make_indexer(tmp_path)
    idx.embedder = _FakeEmbedder(dim=4096)
    idx.store = _FakeStore(tables={"demo-repo__main": 4096})
    idx._validate_embedder_dim("main")


def test_mismatched_dim_on_actual_branch_raises(tmp_path):
    idx = _make_indexer(tmp_path)
    idx.embedder = _FakeEmbedder(dim=4096, model_name="qwen3")
    idx.store = _FakeStore(tables={"demo-repo__main": 768})
    with pytest.raises(ValueError, match="Embedder dimension mismatch"):
        idx._validate_embedder_dim("main")


def test_uses_the_real_branch_not_hardcoded_master(tmp_path):
    """Regression for the literal bug: an existing 768-dim table on the repo's
    real branch ("develop") must be checked -- not skipped because the code was
    looking at a hardcoded "master" table that doesn't exist."""
    idx = _make_indexer(tmp_path)
    idx.embedder = _FakeEmbedder(dim=4096)
    idx.store = _FakeStore(tables={"demo-repo__develop": 768})
    with pytest.raises(ValueError, match="demo-repo@develop"):
        idx._validate_embedder_dim("develop")


def test_does_not_create_a_table_as_a_side_effect(tmp_path):
    """Regression: the old implementation called LanceStore.open(), which creates
    a table if one doesn't exist. Validation must be read-only."""
    idx = _make_indexer(tmp_path)
    idx.embedder = _FakeEmbedder(dim=4096)
    tables = {}
    idx.store = _FakeStore(tables=tables)
    idx._validate_embedder_dim("main")
    assert tables == {}, "validation must never create a table as a side effect"


def test_infra_failure_listing_tables_does_not_raise(tmp_path):
    """A broken/unavailable LanceDB connection is an infra problem, not a
    dimension mismatch -- must log and continue, not raise ValueError."""
    idx = _make_indexer(tmp_path)
    idx.embedder = _FakeEmbedder(dim=4096)

    class _BrokenConnection:
        def table_names(self):
            raise RuntimeError("lancedb unavailable")
    idx.store = type("S", (), {"db": _BrokenConnection()})()
    idx._validate_embedder_dim("main")   # must not raise
