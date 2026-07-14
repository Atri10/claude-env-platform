"""
claude-env :: DI - Dependency Injection Container
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TypeVar

from claudenv.adapters.audit import SqliteAuditLogger
from claudenv.adapters.config import (
    get_config,
    get_database_config,
    get_lancedb_config,
    get_rag_config_provider,
    get_global_policy_config,
    get_repo_policy_config,
    get_mcp_config,
    get_claude_env_home_provider,
)
from claudenv.adapters.embedding import get_embedder, get_reranker
from claudenv.adapters.services import FileServiceRegistry
from claudenv.adapters.persistence import (
    SQLiteAuditRepository, SQLiteDatabase, SQLiteMemoryRepository, SQLiteRagBookkeeping,
    SQLitePolicyRepository,
)
from claudenv.adapters.vector.lancedb import LanceDbVectorStore, LanceDbRagRetriever
from claudenv.application.approval import ApprovalGate
from claudenv.application.rag import RagIndexer, RagService
from claudenv.domain.memory.service import (
    MemoryGraph,
    MemoryServiceImpl,
    MemoryWriter,
    MemoryReader,
    MemoryDecay,
    MemoryGraphTraversal,
)
from claudenv.domain.policy import PolicyEngine, PolicyService
from claudenv.ports import (
    IApprovalGate, IAuditLogger, IAuditRepository,
    IToolAuditLogger, IAgentAuditLogger, ISecurityAuditLogger,
    IApprovalAuditLogger, IVerifiableLedger,
    IConfigProvider, IDatabaseConfig, ILanceDBConfig, IRAGConfig, IGlobalPolicyConfig, IRepoPolicyConfig,
    IMCPConfig, IClaudeEnvHome, IDatabase, IEmbeddingProvider, IEventBus, IMemoryGraph,
    IMemoryRepository, IMemoryService, IRagBookkeeping, IRagIndexer, IRagRetriever,
    IReranker, IServiceRegistry, IVectorStore,
    IPolicyRepository, IPolicyEngine,
    IMemoryWriter, IMemoryReader, IMemoryDecay, IMemoryGraphTraversal,
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

    # --- Focused Config Providers ---
    db_config = get_database_config()
    c.register_singleton(IDatabaseConfig, db_config)

    lancedb_config = get_lancedb_config()
    c.register_singleton(ILanceDBConfig, lancedb_config)

    rag_config = get_rag_config_provider()
    c.register_singleton(IRAGConfig, rag_config)

    global_policy_config = get_global_policy_config()
    c.register_singleton(IGlobalPolicyConfig, global_policy_config)

    repo_policy_config = get_repo_policy_config()
    c.register_singleton(IRepoPolicyConfig, repo_policy_config)

    mcp_config = get_mcp_config()
    c.register_singleton(IMCPConfig, mcp_config)

    claude_env_home = get_claude_env_home_provider()
    c.register_singleton(IClaudeEnvHome, claude_env_home)

    # Also register legacy IConfigProvider for backward compatibility
    legacy_config = get_config()
    c.register_singleton(IConfigProvider, legacy_config)

    # --- Database ---
    db = SQLiteDatabase(db_config.get_database_dsn())
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
        audit_db = SQLiteDatabase(db_config.get_database_dsn())
        return SqliteAuditLogger(
            db=audit_db,
            session_id=SessionId.from_string(session_id),
            actor=actor,
            repo=RepoSlug.from_string(repo) if repo else None,
            tier=Tier(tier) if tier is not None else None,
        )

    c.register_factory(IAuditLogger, lambda: make_audit_logger())

    # Register role-specific audit logger interfaces (all backed by the same SqliteAuditLogger)
    c.register_factory(IToolAuditLogger, lambda: make_audit_logger())
    c.register_factory(IAgentAuditLogger, lambda: make_audit_logger())
    c.register_factory(ISecurityAuditLogger, lambda: make_audit_logger())
    c.register_factory(IApprovalAuditLogger, lambda: make_audit_logger())
    c.register_factory(IVerifiableLedger, lambda: make_audit_logger())

    # --- Memory ---
    mem_repo = SQLiteMemoryRepository(db)
    c.register_singleton(IMemoryRepository, mem_repo)

    # MemoryGraph factory (namespace-scoped)
    def make_memory_graph(namespace: str, session_id: str = "mem",
                          actor: str = "system", isolated: bool = False) -> IMemoryGraph:
        # MemoryGraph's real constructor is (repo, namespace, embedding=,
        # isolated=, ...) -- not (namespace, audit_logger). This previously
        # passed the namespace string as the repository and an audit logger
        # as the namespace, which would crash the instant any method touched
        # self.repo (an IMemoryRepository) or self.namespace.
        embedder_for_memory = None
        try:
            embedder_for_memory = get_embedder()
        except Exception:
            pass
        return MemoryGraph(mem_repo, namespace, embedding=embedder_for_memory, isolated=isolated)

    c.register_factory(IMemoryGraph, lambda: make_memory_graph("default"))

    # IMemoryService (domain implementation)
    memory_service = MemoryServiceImpl(mem_repo, legacy_config)
    c.register_singleton(IMemoryService, memory_service)

    # Focused Memory Ports (namespace-scoped factories)
    def make_memory_writer(namespace: str = "default") -> IMemoryWriter:
        embedder_for_memory = None
        try:
            embedder_for_memory = get_embedder()
        except Exception:
            pass
        return MemoryWriter(mem_repo, namespace, embedding=embedder_for_memory)

    def make_memory_reader(
            namespace: str = "default",
            isolated: bool = False,
            shared_with: tuple[str, ...] = (),
    ) -> IMemoryReader:
        return MemoryReader(mem_repo, namespace, isolated=isolated, shared_namespaces=shared_with)

    def make_memory_decay() -> IMemoryDecay:
        return MemoryDecay(mem_repo)

    def make_memory_traversal(
            namespace: str = "default",
            isolated: bool = False,
            shared_with: tuple[str, ...] = (),
    ) -> IMemoryGraphTraversal:
        embedder_for_memory = None
        try:
            embedder_for_memory = get_embedder()
        except Exception:
            pass
        return MemoryGraphTraversal(mem_repo, namespace, embedding=embedder_for_memory, isolated=isolated, shared_namespaces=shared_with)

    c.register_factory(IMemoryWriter, lambda: make_memory_writer("default"))
    c.register_factory(IMemoryReader, lambda: make_memory_reader("default"))
    c.register_factory(IMemoryDecay, make_memory_decay)
    c.register_factory(IMemoryGraphTraversal, lambda: make_memory_traversal("default"))

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
        store = c.get(IVectorStore)
        return RagIndexer(
            repo=RepoSlug.from_string(repo),
            branch=BranchName.from_string(branch),
            store=store,
            bookkeeping=rag_bookkeeping,
            embedder=embedder,
            chunker=rag_config.get_rag_config(),
        )

    def make_rag_service(repo: str, branch: str = "main") -> RagService:
        indexer = make_rag_indexer(repo, branch)
        store = c.get(IVectorStore)
        # Wrap the raw vector store in LanceDbRagRetriever so query embedding
        # and reranking actually run on search (passing `store` directly here
        # would silently skip reranking -- LanceDbVectorStore.search() doesn't
        # know about the reranker at all).
        retriever = LanceDbRagRetriever(store, embedder, reranker)
        return RagService(indexer, retriever, embedder, reranker, bookkeeping=rag_bookkeeping)

    c.register_factory(IRagIndexer, lambda: make_rag_indexer("default"))
    c.register_factory(IRagRetriever, lambda: make_rag_service("default"))
    c.register_factory(RagService, lambda: make_rag_service("default"))

    # --- Policy ---
    policy_service = PolicyService(legacy_config)
    c.register_singleton(PolicyService, policy_service)

    # PolicyEngine is the evaluator; load it lazily per repo root.
    c.register_factory(IPolicyEngine, lambda: policy_service.load_engine("."))

    # --- Approval ---
    c.register_factory(
        IApprovalGate,
        lambda: ApprovalGate(make_audit_logger(session_id="approval-gate", actor="approval-gate"), db),
    )

    # --- Services ---
    c.register_singleton(IServiceRegistry, FileServiceRegistry(claude_env_home.get_claude_env_home()))

    # --- Event Bus ---
    c.register_singleton(IEventBus, EventBus())

    # --- Vector Store ---
    c.register_factory(IVectorStore, lambda: LanceDbVectorStore(
        lancedb_config.get_lancedb_path(), rag_config.get_rag_config().embedding_dim, db
    ))

    # --- Policy Repository ---
    policy_repo = SQLitePolicyRepository(db)
    c.register_singleton(IPolicyRepository, policy_repo)


# ============================================================================
# Utility Classes
# ============================================================================

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