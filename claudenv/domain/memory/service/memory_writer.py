"""
claude-env :: Domain - Memory Service (Domain Layer) - MemoryWriter
"""
from __future__ import annotations

import json
import struct
from typing import Any

from claudenv.domain.memory import (
    EdgeRelation, MemoryEdge, MemoryNode,
    MemoryType, NodeKind,
    CrossNamespaceEdge, InvalidMemoryRelation,
    validate_memory_kind,
)
from claudenv.domain.value_objects import NodeId, utc_now
from claudenv.domain.memory.service.i_memory_writer import IMemoryWriter
from claudenv.domain.memory.service.i_memory_repository import IMemoryRepository
from claudenv.domain.memory.service.i_embedding_provider import IEmbeddingProvider


class MemoryWriter(IMemoryWriter):
    """Write operations for memory nodes."""

    def __init__(
            self,
            repo: IMemoryRepository,
            namespace: str,
            embedding: IEmbeddingProvider | None = None,
    ):
        self.repo = repo
        self.namespace = namespace
        self.embedding = embedding

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

        # Generate embedding if not provided. The embedding provider exposes
        # embed_documents()/embed_query() returning list[float] -- there is no
        # embed_text() method -- so we embed via embed_documents() and pack the
        # vector to bytes for storage/struct.unpack in recall().
        if embedding is None and self.embedding:
            text = name + " " + json.dumps(body)
            try:
                vec = self.embedding.embed_documents([text])[0]
                embedding = struct.pack(f"<{len(vec)}f", *vec)
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
