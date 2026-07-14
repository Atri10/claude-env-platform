"""
claude-env :: Ports - Consumer-owned interfaces
"""
from __future__ import annotations

from .approval import IApprovalGate, IApprovalNotifier, IApprovalUI, IApprovalRepository
from .audit import (
    IAuditLogger, IAuditRepository,
    IToolAuditLogger, IAgentAuditLogger, ISecurityAuditLogger,
    IApprovalAuditLogger, IVerifiableLedger,
)
from .config import (
    IConfigProvider, IDatabaseConfig, ILanceDBConfig, IRAGConfig,
    IGlobalPolicyConfig, IRepoPolicyConfig, IMCPConfig, IClaudeEnvHome,
)
from .database import IDatabase, ITransaction
from .events import IEventBus
from .hooks import IPreToolUseHook, IPostToolUseHook, HookInstaller
from .memory import (
    IMemoryGraph, IMemoryRepository, IEmbeddingProvider, IMemoryService,
    IMemoryWriter, IMemoryReader, IMemoryDecay, IMemoryGraphTraversal,
)
from .observability import (
    IBudgetConfig, IMetricsRepository, IFeedbackRepository, IProjectionRepository,
)
from .policy import IPolicyEngine, IPolicyRepository, IPolicySimulator
from .rag import (
    IRagIndexer, IRagRetriever, IRagBookkeeping, IReranker, IVectorStore,
)
from .services import IServiceRegistry

__all__ = [
    # Config
    "IConfigProvider",
    "IDatabaseConfig", "ILanceDBConfig", "IRAGConfig",
    "IGlobalPolicyConfig", "IRepoPolicyConfig", "IMCPConfig", "IClaudeEnvHome",
    # Database
    "IDatabase", "ITransaction",
    # Policy
    "IPolicyEngine", "IPolicyRepository", "IPolicySimulator",
    # Memory
    "IMemoryGraph", "IMemoryRepository", "IEmbeddingProvider", "IMemoryService",
    "IMemoryWriter", "IMemoryReader", "IMemoryDecay", "IMemoryGraphTraversal",
    # RAG
    "IRagIndexer", "IRagRetriever", "IRagBookkeeping", "IReranker", "IVectorStore",
    # Observability
    "IBudgetConfig", "IMetricsRepository", "IFeedbackRepository", "IProjectionRepository",
    # Audit
    "IAuditLogger", "IAuditRepository",
    "IToolAuditLogger", "IAgentAuditLogger", "ISecurityAuditLogger",
    "IApprovalAuditLogger", "IVerifiableLedger",
    # Approval
    "IApprovalGate", "IApprovalNotifier", "IApprovalUI", "IApprovalRepository",
    # Events
    "IEventBus",
    # Services
    "IServiceRegistry",
    # Hooks
    "IPreToolUseHook", "IPostToolUseHook", "HookInstaller",
]
