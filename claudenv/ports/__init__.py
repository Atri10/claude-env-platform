"""
claude-env :: Ports - Consumer-owned interfaces
"""
from __future__ import annotations

from .approval import IApprovalGate, IApprovalNotifier, IApprovalUI, IApprovalRepository
from .audit import IAuditLogger, IAuditRepository
from .bootstrap import (
    BootstrapContext,
    BootstrapResult,
    IBootstrapOrchestrator,
    IBootstrapStep,
)
from .config import IConfigProvider
from .database import IDatabase, ITransaction
from .events import IEventBus
from .hooks import IPreToolUseHook, IPostToolUseHook, HookInstaller
from .memory import (
    IMemoryGraph, IMemoryRepository, IEmbeddingProvider,
)
from .policy import IPolicyEngine, IPolicyRepository, IPolicySimulator
from .rag import (
    IRagIndexer, IRagRetriever, IRagBookkeeping, IReranker,
)
from .services import IServiceRegistry

__all__ = [
    # Config
    "IConfigProvider",
    # Database
    "IDatabase", "ITransaction",
    # Policy
    "IPolicyEngine", "IPolicyRepository", "IPolicySimulator",
    # Memory
    "IMemoryGraph", "IMemoryRepository", "IEmbeddingProvider",
    # RAG
    "IRagIndexer", "IRagRetriever", "IRagBookkeeping", "IReranker",
    # Audit
    "IAuditLogger", "IAuditRepository",
    # Approval
    "IApprovalGate", "IApprovalNotifier", "IApprovalUI", "IApprovalRepository",
    # Events
    "IEventBus",
    # Services
    "IServiceRegistry",
    # Hooks
    "IPreToolUseHook", "IPostToolUseHook", "HookInstaller",
    # Bootstrap
    "BootstrapContext",
    "BootstrapResult",
    "IBootstrapOrchestrator",
    "IBootstrapStep",
]
