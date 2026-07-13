"""
claude-env :: Application - Memory Operations
"""
from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from claudenv.domain.memory import (
    EdgeRelation, MemoryEdge, MemoryNode,
    MemoryType, NodeKind, NamespaceConfig, VALID_KINDS,
    CrossNamespaceEdge, InvalidMemoryKind, InvalidMemoryRelation,
    effective_confidence, validate_memory_kind,
)
from claudenv.domain.value_objects import (
    ContentHash, EdgeId, MemoryType, NodeId, RepoSlug, Tier, utc_now,
)
from claudenv.ports import (
    IConfigProvider, IEmbeddingProvider, IMemoryGraph, IMemoryRepository,
)


def validate_memory_kind(memory_type: MemoryType, node_kind: NodeKind) -> None:
    """Validate that (memory_type, node_kind) is a valid combination."""
    allowed = VALID_KINDS.get(memory_type)
    if allowed is None or node_kind not in allowed:
        raise InvalidMemoryKind(
            f"Invalid combination: memory_type={memory_type.value}, "
            f"node_kind={node_kind.value}. "
            f"Valid kinds for {memory_type.value}: {', '.join(k.value for k in allowed)}"
        )


class MemoryGraph(IMemoryGraph):
    """In-memory graph operations with repository persistence."""

    def __init__(
            self,
            repo: IMemoryRepository,
            namespace: str,
            embedding: IEmbeddingProvider | None = None,
            isolated: bool = False,
            shared_namespaces: tuple[str, ...] = (),
            tier: Tier = Tier.INTERNAL,
    ):
        self.repo = repo
        self.namespace = namespace
        self.embedding = embedding
        self.isolated = isolated
        self.shared_namespaces = shared_namespaces
        self.tier = tier

    def _allowed_namespaces(self) -> list[str]:
        """Namespaces this graph can read from."""
        namespaces = [self.namespace]
        if not self.isolated:
            namespaces.extend(self.shared_namespaces)
        return namespaces

    def add_node(
            self,
            memory_type: MemoryType,
            node_kind: str,
            name: str,
            body: dict[str, Any],
            repo: str | None = None,
            confidence: float = 1.0,
            embedding: bytes | None = None,
    ) -> NodeId:
        node_kind_enum = NodeKind(node_kind)
        validate_memory_kind(memory_type, node_kind_enum)

        # Generate embedding if not provided
        if embedding is None and self.embedding:
            text = name + " " + json.dumps(body)
            try:
                embedding = self.embedding.embed_text(text)
            except Exception:
                pass

        node = MemoryNode.create(
            namespace=self.namespace,
            memory_type=memory_type,
            node_kind=node_kind_enum,
            name=name,
            body=body,
            repo=repo,
            confidence=confidence,
            embedding=embedding,
        )
        self.repo.insert_node(node)
        return node.node_id

    def get_node(self, node_id: NodeId) -> MemoryNode | None:
        return self.repo.get_node(node_id)

    def supersede(
            self,
            old_node_id: NodeId,
            memory_type: MemoryType,
            node_kind: str,
            name: str,
            body: dict[str, Any],
            **kwargs,
    ) -> NodeId:
        node_kind_enum = NodeKind(node_kind)
        new_node = self.add_node(memory_type, node_kind_enum.value, name, body, **kwargs)

        # Link SUPERSEDES
        self.repo.insert_edge(MemoryEdge.create(
            namespace=self.namespace,
            src=new_node,
            dst=old_node_id,
            relation=EdgeRelation.SUPERSEDES,
        ))

        # Mark old as superseded
        old = self.repo.get_node(old_node_id)
        if old:
            from dataclasses import replace
            updated = replace(old, superseded_by=str(new_node), updated_at=utc_now())
            self.repo.update_node(updated)

        return new_node

    def add_edge(
            self,
            src: NodeId,
            dst: NodeId,
            relation: str,
            weight: float = 1.0,
    ) -> str:
        rel = EdgeRelation(relation)
        if rel not in EdgeRelation:
            raise InvalidMemoryRelation(f"Invalid relation: {relation}")

        # Verify both nodes exist and are in same namespace
        src_node = self.repo.get_node(src)
        dst_node = self.repo.get_node(dst)
        if not src_node or not dst_node:
            raise CrossNamespaceEdge("Edge endpoint does not exist")
        if src_node.namespace != self.namespace or dst_node.namespace != self.namespace:
            raise CrossNamespaceEdge("Cross-namespace edge not allowed")

        edge = MemoryEdge.create(
            namespace=self.namespace,
            src=src, dst=dst, relation=rel, weight=weight,
        )
        self.repo.insert_edge(edge)
        return str(edge.edge_id)

    def recall(
            self,
            query: str,
            depth: int = 2,
            top_k: int = 10,
            query_vector: list[float] | None = None,
            extra_namespaces: list[str] | None = None,
    ) -> list[MemoryNode]:
        # Keyword recall
        seeds = self.repo.list_nodes(
            namespace=self.namespace,
            memory_type=None,
            min_confidence=0.0,
            limit=top_k,
        )
        query_lower = query.lower()
        seeds = [n for n in seeds if query_lower in n.name.lower() or query_lower in json.dumps(n.body).lower()]

        # Embedding recall if available
        if query_vector and self.embedding:
            emb_seeds = self.repo.list_nodes(
                namespace=self.namespace,
                memory_type=None,
                min_confidence=0.0,
                limit=top_k,
            )
            scored = []
            for n in emb_seeds:
                if n.embedding:
                    vec = list(struct.unpack(f"<{len(n.embedding) // 4}f", n.embedding))
                    # cosine similarity
                    dot = sum(a * b for a, b in zip(query_vector, vec))
                    norm_a = sum(x * x for x in query_vector) ** 0.5
                    norm_b = sum(x * x for x in vec) ** 0.5
                    if norm_a > 0 and norm_b > 0:
                        scored.append((n, dot / (norm_a * norm_b)))
            scored.sort(key=lambda x: x[1], reverse=True)
            for n, _ in scored[:top_k]:
                if n not in seeds:
                    seeds.append(n)

        # Expand graph
        seen = {n.node_id: n for n in seeds}
        seed_ids = list(seen.keys())
        expanded = self.repo.expand_graph(
            seed_ids=seed_ids,
            depth=depth,
            relations=None,
            namespace=self.namespace,
            extra_namespaces=extra_namespaces or self.shared_namespaces,
        )
        for n in expanded:
            seen.setdefault(n.node_id, n)

        # Rank by effective confidence
        result = list(seen.values())
        result.sort(key=lambda x: x.effective_confidence, reverse=True)
        return result[:top_k]

    def expand(
            self,
            seed_ids: list[NodeId],
            depth: int = 2,
            relations: list[str] | None = None,
            extra_namespaces: list[str] | None = None,
    ) -> list[MemoryNode]:
        return self.repo.expand_graph(
            seed_ids=seed_ids,
            depth=depth,
            relations=relations,
            namespace=self.namespace,
            extra_namespaces=extra_namespaces or self.shared_namespaces,
        )

    def list_nodes(
            self,
            memory_type: MemoryType | None = None,
            min_confidence: float = 0.0,
            limit: int = 100,
    ) -> list[MemoryNode]:
        return self.repo.list_nodes(
            namespace=self.namespace,
            memory_type=memory_type,
            min_confidence=min_confidence,
            include_superseded=False,
            limit=limit,
        )

    def decay(self) -> int:
        return self.repo.decay_all(self.namespace)

    def get_namespace_config(self) -> NamespaceConfig:
        return NamespaceConfig(
            name=self.namespace,
            isolated=self.isolated,
            tier=self.tier,
            shared_with=self.shared_namespaces,
        )


class MemoryService:
    """Application service for memory operations."""

    def __init__(
            self,
            repo: IMemoryRepository,
            config: IConfigProvider,
    ):
        self.repo = repo
        self.config = config

    def create_graph(
            self,
            namespace: str,
            isolated: bool = False,
            tier: Tier = Tier.INTERNAL,
            shared_with: tuple[str, ...] = (),
    ) -> MemoryGraph:
        embedding = None
        try:
            from claudenv.adapters.embedding import get_embedder
            embedding = get_embedder()
        except Exception:
            pass

        return MemoryGraph(
            repo=self.repo,
            namespace=namespace,
            embedding=embedding,
            isolated=isolated,
            shared_namespaces=shared_with,
            tier=tier,
        )

    def get_project_graph(self, repo_slug: RepoSlug, tier: Tier) -> MemoryGraph:
        isolated = tier >= Tier.SENSITIVE
        return self.create_graph(
            namespace=f"proj-{repo_slug}",
            isolated=isolated,
            tier=tier,
        )

    def get_agent_graph(self, agent_id: str) -> MemoryGraph:
        return self.create_graph(
            namespace=f"agent:{agent_id}",
            isolated=True,
            tier=Tier.INTERNAL,
        )

    def consolidate(self, namespace: str, dry_run: bool = False) -> dict[str, Any]:
        """Run memory consolidation."""
        consolidator = MemoryConsolidator(self.repo, namespace)
        return consolidator.run(dry_run=dry_run)

    def prune(self, namespace: str, dry_run: bool = True) -> dict[str, Any]:
        """Run memory pruning."""
        pruner = MemoryPruner(self.repo, namespace)
        return pruner.run(dry_run=dry_run)

    def sync_export(self, namespace: str) -> list[dict[str, Any]]:
        """Export memory namespace."""
        sync = MemorySync(self.repo, namespace)
        return sync.export()

    def sync_import(self, namespace: str, data: list[dict[str, Any]]) -> int:
        """Import memory namespace."""
        sync = MemorySync(self.repo, namespace)
        return sync.import_data(data)


# ============================================================================
# Memory Maintenance Operations
# ============================================================================

class MemoryConsolidator:
    """Consolidate low-value memory clusters into summary nodes."""

    def __init__(self, repo: IMemoryRepository, namespace: str):
        self.repo = repo
        self.namespace = namespace

    def run(self, dry_run: bool = False) -> dict[str, Any]:
        """Find and consolidate low-value clusters."""
        nodes = self.repo.list_nodes(
            namespace=self.namespace,
            min_confidence=0.0,
            include_superseded=False,
            limit=1000,
        )

        # Group by kind and look for clusters
        clusters: dict[str, list[MemoryNode]] = {}
        for n in nodes:
            key = f"{n.memory_type.value}:{n.node_kind.value}"
            clusters.setdefault(key, []).append(n)

        consolidated = 0
        for kind, cluster_nodes in clusters.items():
            if len(cluster_nodes) < 5:
                continue
            # Find low-confidence, old nodes
            candidates = [n for n in cluster_nodes if n.effective_confidence < 0.3]
            if len(candidates) < 3:
                continue

            # Create summary node
            summary_name = f"Consolidated {kind} ({len(candidates)} nodes)"
            summary_body = {
                "consolidated_from": [str(n.node_id) for n in candidates],
                "original_count": len(candidates),
                "consolidated_at": utc_now().isoformat(),
            }

            if not dry_run:
                new_id = MemoryNode.create(
                    namespace=self.namespace,
                    memory_type=candidates[0].memory_type,
                    node_kind=candidates[0].node_kind,
                    name=summary_name,
                    body=summary_body,
                    repo=candidates[0].repo,
                    confidence=0.5,
                ).node_id
                # Link CONSOLIDATES
                for c in candidates:
                    self.repo.insert_edge(MemoryEdge.create(
                        namespace=self.namespace,
                        src=new_id, dst=c.node_id,
                        relation=EdgeRelation.CONSOLIDATES,
                    ))
            consolidated += 1

        return {"consolidated_clusters": consolidated, "dry_run": dry_run}


class MemoryPruner:
    """Prune low-value, superseded, or expired memories."""

    def __init__(self, repo: IMemoryRepository, namespace: str):
        self.repo = repo
        self.namespace = namespace

    def run(self, dry_run: bool = True) -> dict[str, Any]:
        """Prune low-confidence, superseded, or expired nodes."""
        nodes = self.repo.list_nodes(
            namespace=self.namespace,
            min_confidence=0.0,
            include_superseded=True,
            limit=5000,
        )

        to_prune = []
        for n in nodes:
            # Never prune decision/architecture nodes
            if n.node_kind in (NodeKind.DECISION, NodeKind.ARCHITECTURE):
                continue
            # Prune superseded
            if n.is_superseded():
                to_prune.append(n)
            # Prune very low confidence old nodes
            elif n.effective_confidence < 0.1 and n.node_kind not in (NodeKind.DECISION, NodeKind.ARCHITECTURE):
                to_prune.append(n)

        pruned = 0
        if not dry_run:
            for n in to_prune:
                # In production, would archive first
                self.repo.delete_node(n.node_id)  # Need to add this method
                pruned += 1

        return {"candidates": len(to_prune), "pruned": pruned, "dry_run": dry_run}


class MemorySync:
    """Export/import memory namespaces for team sync."""

    def __init__(self, repo: IMemoryRepository, namespace: str):
        self.repo = repo
        self.namespace = namespace

    def export(self) -> list[dict[str, Any]]:
        """Export all nodes and edges in namespace."""
        nodes = self.repo.list_nodes(
            namespace=self.namespace,
            include_superseded=True,
            limit=10000,
        )
        edges = []
        for n in nodes:
            node_edges = self.repo.get_edges(src=n.node_id)
            edges.extend(node_edges)

        return {
            "namespace": self.namespace,
            "nodes": [n.to_dict() for n in nodes],
            "edges": [e.to_dict() for e in edges],
            "exported_at": utc_now().isoformat(),
        }

    def import_data(self, data: dict[str, Any]) -> int:
        """Import nodes and edges (additive, skips existing IDs)."""
        imported = 0
        for node_data in data.get("nodes", []):
            node = MemoryNode.from_dict(node_data)
            if not self.repo.get_node(node.node_id):
                self.repo.insert_node(node)
                imported += 1

        for edge_data in data.get("edges", []):
            edge = MemoryEdge.from_dict(edge_data)
            # Check if edge already exists
            existing = self.repo.get_edges(src=edge.src, dst=edge.dst)
            if not any(e.relation == edge.relation for e in existing):
                self.repo.insert_edge(edge)

        return imported
