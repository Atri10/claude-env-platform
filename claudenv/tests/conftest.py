"""
claude-env :: Tests - Shared Fixtures
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from claudenv import di
from claudenv.adapters.config import reset_config_providers
from claudenv.adapters.embedding.factory import reload_embedder, reload_reranker
from claudenv.adapters.persistence.sqlite.database import SQLiteDatabase

TEST_ENV_HOME = "/tmp/claude-env-test"


@pytest.fixture
def isolated_env() -> None:
    """Isolate test to temporary CLAUDE_ENV_HOME and reset singletons."""
    os.environ["CLAUDE_ENV_HOME"] = TEST_ENV_HOME
    yield
    di.reset_container()
    reset_config_providers()
    reload_embedder()
    reload_reranker()


@pytest.fixture
def temp_db() -> SQLiteDatabase:
    """In-memory SQLite database for isolated tests."""
    db = SQLiteDatabase("sqlite:///:memory:")
    yield db
    db.close()


@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    """Temporary SQLite database file path."""
    return tmp_path / "test.db"
