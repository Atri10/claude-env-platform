"""
claude-env :: Ports - Consumer-owned interfaces
"""
from __future__ import annotations

from .approval import IApprovalGate, IApprovalNotifier, IApprovalRepository, IApprovalUI
from .audit import (
    IAgentAuditLogger,
    IApprovalAuditLogger,
    IAuditLogger,
    IAuditRepository,
    ISecurityAuditLogger,
    IToolAuditLogger,
    IVerifiableLedger,
)
from .config import (
    IClaudeEnvHome,
    IConfigProvider,
    IDatabaseConfig,
    IGlobalPolicyConfig,
    ILanceDBConfig,
    IMCPConfig,
    IRAGConfig,
    IRepoPolicyConfig,
)
from .database import IDatabase, ITransaction
from .events import IEventBus
from .hooks import HookInstaller, IPostToolUseHook, IPreToolUseHook
from .memory import (
    IEmbeddingProvider,
    IMemoryDecay,
    IMemoryGraph,
    IMemoryGraphTraversal,
    IMemoryReader,
    IMemoryRepository,
    IMemoryService,
    IMemoryWriter,
)
from .observability import (
    IBudgetConfig,
    IFeedbackRepository,
    IMetricsRepository,
    IProjectionRepository,
)
from .policy import IPolicyEngine, IPolicyRepository, IPolicySimulator
from .rag import (
    IRagBookkeeping,
    IRagIndexer,
    IRagRetriever,
    IReranker,
    IVectorStore,
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
