"""
claude-env :: Ports - Consumer-owned interfaces
"""
from __future__ import annotations

from .config import IConfigProvider
from .database import IDatabase, ITransaction
from .policy import IPolicyEngine, IPolicyRepository, IPolicySimulator
from .memory import (
    IMemoryGraph, IMemoryRepository, IEmbeddingProvider,
)
from .rag import (
    IRagIndexer, IRagRetriever, IRagBookkeeping, IReranker,
)
from .audit import IAuditLogger, IAuditRepository
from .approval import IApprovalGate, IApprovalNotifier, IApprovalUI, IApprovalRepository
from .events import IEventBus
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
]