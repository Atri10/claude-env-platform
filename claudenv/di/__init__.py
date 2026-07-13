"""
claude-env :: DI - Dependency Injection Container
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TypeVar

from claudenv.adapters.audit import SqliteAuditLogger
from claudenv.adapters.config import get_config
from claudenv.adapters.embedding import get_embedder, get_reranker
from claudenv.adapters.persistence import (
    SQLiteAuditRepository, SQLiteDatabase, SQLiteMemoryRepository, SQLiteRagBookkeeping,
)
from claudenv.application.memory import MemoryGraph
from claudenv.application.rag import RagIndexer, RagService
from claudenv.domain.policy import PolicyEngine, PolicyService
from claudenv.ports import (
    IAuditLogger, IAuditRepository, IConfigProvider, IDatabase, IEmbeddingProvider,
    IEventBus, IMemoryGraph, IMemoryRepository, IRagBookkeeping, IRagIndexer,
    IRagRetriever, IReranker, IServiceRegistry,
)

T = TypeVar("T")


class Container:
    """Simple dependency injection container."""

    def __init__(self):
        self._singletons: dict[type, Any] = {}
        self._factories: dict[type, Callable[[], Any]] = {}
        self._lock = threading.RLock()

    def register_singleton(self, interface: type[T], implementation: T) -> None:
        with self._lock:
            self._singletons[interface] = implementation

    def register_factory(self, interface: type[T], factory: Callable[[], T]) -> None:
        with self._lock:
            self._factories[interface] = factory

    def get(self, interface: type[T]) -> T:
        with self._lock:
            if interface in self._singletons:
                return self._singletons[interface]

            if interface in self._factories:
                instance = self._factories[interface]()
                self._singletons[interface] = instance
                return instance

            raise KeyError(f"No registration for {interface}")

    def get_or_create(self, interface: type[T], factory: Callable[[], T]) -> T:
        with self._lock:
            if interface in self._singletons:
                return self._singletons[interface]
            instance = factory()
            self._singletons[interface] = instance
            return instance


# Global container instance
_container: Container | None = None
_container_lock = threading.Lock()


def get_container() -> Container:
    """Get the global container instance."""
    global _container
    with _container_lock:
        if _container is None:
            _container = Container()
            _configure_container(_container)
        return _container


def reset_container() -> None:
    """Reset the global container (for testing)."""
    global _container
    with _container_lock:
        _container = None


def _configure_container(c: Container) -> None:
    """Configure all service registrations."""

    # --- Config ---
    config = get_config()
    c.register_singleton(IConfigProvider, config)

    # --- Database ---
    db = SQLiteDatabase(config.get_database_dsn())
    c.register_singleton(IDatabase, db)

    # --- Audit ---
    audit_repo = SQLiteAuditRepository(db)
    c.register_singleton(IAuditRepository, audit_repo)

    # AuditLogger factory (session-scoped)
    def make_audit_logger(session_id: str = "default", actor: str = "system",
                          repo: str | None = None, tier: int | None = None) -> IAuditLogger:
        from claudenv.domain.value_objects import RepoSlug, SessionId, Tier
        from claudenv.adapters.persistence import SQLiteDatabase
        # Create a new database connection for the audit logger
        audit_db = SQLiteDatabase(config.get_database_dsn())
        return SqliteAuditLogger(
            db=audit_db,
            session_id=SessionId.from_string(session_id),
            actor=actor,
            repo=RepoSlug.from_string(repo) if repo else None,
            tier=Tier(tier) if tier is not None else None,
        )

    c.register_factory(IAuditLogger, lambda: make_audit_logger())

    # --- Memory ---
    mem_repo = SQLiteMemoryRepository(db)
    c.register_singleton(IMemoryRepository, mem_repo)

    # MemoryGraph factory (namespace-scoped)
    def make_memory_graph(namespace: str, session_id: str = "mem",
                          actor: str = "system", isolated: bool = False) -> IMemoryGraph:
        audit = make_audit_logger(session_id, actor)
        return MemoryGraph(namespace, audit)

    c.register_factory(IMemoryGraph, lambda: make_memory_graph("default"))

    # --- RAG ---
    rag_bookkeeping = SQLiteRagBookkeeping(db)
    c.register_singleton(IRagBookkeeping, rag_bookkeeping)

    # Embedder & Reranker
    embedder = get_embedder()
    c.register_singleton(IEmbeddingProvider, embedder)

    reranker = get_reranker()
    c.register_singleton(IReranker, reranker)

    # RagIndexer & RagService (repo-scoped factories)
    def make_rag_indexer(repo: str, branch: str = "main") -> IRagIndexer:
        from claudenv.domain.value_objects import RepoSlug, BranchName
        from claudenv.adapters.vector.lancedb import LanceDbVectorStore
        store = LanceDbVectorStore(config.get_lancedb_path(), config.get_rag_config().embedding_dim)
        return RagIndexer(
            repo=RepoSlug.from_string(repo),
            branch=BranchName.from_string(branch),
            store=store,
            bookkeeping=rag_bookkeeping,
            embedder=embedder,
            chunker=config.get_rag_config(),
        )

    def make_rag_service(repo: str, branch: str = "main") -> RagService:
        indexer = make_rag_indexer(repo, branch)
        from claudenv.adapters.vector.lancedb import LanceDbVectorStore
        store = LanceDbVectorStore(config.get_lancedb_path(), config.get_rag_config().embedding_dim)
        return RagService(indexer, store, embedder, reranker)

    c.register_factory(IRagIndexer, lambda: make_rag_indexer("default"))
    c.register_factory(IRagRetriever, lambda: make_rag_service("default"))

    # --- Policy ---
    policy_service = PolicyService(config)
    c.register_singleton(PolicyService, policy_service)

    def make_policy_engine(repo_root: str) -> PolicyEngine:
        return PolicyEngine.load(repo_root)

    c.register_factory(PolicyEngine, lambda: make_policy_engine("."))

    # --- Approval ---
    # TODO: register approval gate, notifier, UI

    # --- Services ---
    c.register_singleton(IServiceRegistry, ServiceRegistry())

    # --- Event Bus ---
    c.register_singleton(IEventBus, EventBus())


# ============================================================================
# Utility Classes
# ============================================================================

class ServiceRegistry:
    """Local service discovery (ports, PIDs)."""

    def __init__(self):
        self._services: dict[str, dict] = {}

    def register(self, name: str, port: int, pid: int | None = None, extra: dict | None = None) -> dict:
        entry = {
            "name": name, "host": "127.0.0.1", "port": port,
            "url": f"http://127.0.0.1:{port}", "pid": pid or os.getpid(),
            "started_at": os.environ.get("CLAUDE_ENV_SESSION", ""),
        }
        if extra:
            entry.update(extra)
        self._services[name] = entry
        return entry

    def unregister(self, name: str) -> None:
        self._services.pop(name, None)

    def get(self, name: str) -> dict | None:
        return self._services.get(name)

    def list_live(self) -> list[dict]:
        # Filter dead services
        live = []
        for name, entry in self._services.items():
            pid = entry.get("pid")
            if pid:
                try:
                    os.kill(pid, 0)
                    live.append(entry)
                except (ProcessLookupError, PermissionError):
                    pass
            else:
                live.append(entry)
        return sorted(live, key=lambda e: e.get("name", ""))


class EventBus:
    """Simple in-process event bus."""

    def __init__(self):
        self._subscribers: dict[str, list[Callable]] = {}

    def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        for handler in self._subscribers.get(event_type, []):
            try:
                handler(payload)
            except Exception:
                pass  # Don't let handler errors break the publisher

    def subscribe(self, event_type: str, handler: Callable[[dict], None]) -> None:
        self._subscribers.setdefault(event_type, []).append(handler)

    def unsubscribe(self, event_type: str, handler: Callable[[dict], None]) -> None:
        if event_type in self._subscribers:
            self._subscribers[event_type] = [h for h in self._subscribers[event_type] if h != handler]
