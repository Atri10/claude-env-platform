"""
claude-env :: Domain - Memory Service (Domain Layer) - Validator

Pure domain validator for memory-graph integrity. No I/O: operates solely on
``MemoryNode`` / ``MemoryEdge`` domain objects.

Enforces the edge validation rules that the write path (``MemoryWriter.add_edge``)
checks one-at-a-time, plus the graph-level integrity scan ported from the flat
``memory/memory_validator.py``:

  * valid relation types  — ``edge.relation`` must be a member of ``EdgeRelation``
  * no self-loops          — ``edge.src != edge.dst``
  * namespace isolation    — edge namespace matches both endpoints; no cross-ns leak
  * tier constraints       — an edge cannot join a lower tier into a higher
                             (more restricted) tier unless the destination
                             namespace is explicitly shared
  * dangling edges         — both endpoints must exist in the supplied node set
  * confidence in [0, 1]   — stored confidence is a probability
  * supersede acyclicity   — the SUPERSEDES chain must not form a cycle

Two entry points:

  * ``validate_edge``  — raises on the first violation (write-time strict path).
  * ``validate_graph`` — collects every issue into a ``ValidationResult``
                         (integrity / audit scan).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from claudenv.domain.memory import (
    CrossNamespaceEdge,
    EdgeRelation,
    InvalidMemoryRelation,
    MemoryEdge,
    MemoryNode,
    NamespaceConfig,
)
from claudenv.domain.memory.errors import InvalidMemoryKind
from claudenv.domain.memory.helpers import validate_memory_kind
from claudenv.domain.value_objects import NodeId

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

#: Category labels for the issue ``kind`` field, mirroring the flat validator's
#: issue dict keys so audit/reporting layers can group identically.
KIND_INVALID_RELATION = "invalid_relation"
KIND_SELF_LOOP = "self_loop"
KIND_CROSS_NAMESPACE = "ns_leak_edge"
KIND_DANGLING_EDGE = "dangling_edge"
KIND_BAD_CONFIDENCE = "bad_confidence"
KIND_BAD_KIND = "bad_kind"
KIND_SUPERSEDE_CYCLE = "supersede_cycle"
KIND_TIER_VIOLATION = "tier_violation"


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """A single integrity finding."""

    kind: str
    edge_id: str | None = None
    node_id: str | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Aggregated integrity report for a graph scan."""

    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when no issues were found."""
        return not self.issues

    def counts(self) -> dict[str, int]:
        """Per-kind issue counts (zero-valued keys omitted)."""
        out: dict[str, int] = {}
        for issue in self.issues:
            out[issue.kind] = out.get(issue.kind, 0) + 1
        return out


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


class MemoryValidator:
    """Pure domain validator for memory-graph integrity.

    Stateless: safe to instantiate once and reuse across namespaces.
    """

    # -- write-time strict path --------------------------------------------

    @staticmethod
    def validate_edge(
            edge: MemoryEdge,
            src_node: MemoryNode | None,
            dst_node: MemoryNode | None,
            ns_config: NamespaceConfig | None = None,
    ) -> None:
        """Raise on the first edge violation.

        Called at write time (before persisting an edge) so bad edges never
        enter the graph.  Raises :class:`InvalidMemoryRelation`,
        :class:`CrossNamespaceEdge`, or :class:`InvalidMemoryKind`.
        """
        # 1. relation type — EdgeRelation is a closed enum, so a bad string
        #    fails at construction; re-check membership for symmetry with the
        #    flat validator's VALID_RELS set.
        if edge.relation not in EdgeRelation:
            raise InvalidMemoryRelation(
                f"invalid relation={edge.relation!r}; expected one of "
                f"{sorted(r.value for r in EdgeRelation)}"
            )

        # 2. self-loop
        if edge.src == edge.dst:
            raise CrossNamespaceEdge(
                f"self-loop on {edge.src!r} is not allowed"
            )

        # 3. dangling endpoints — both nodes must exist
        if src_node is None:
            raise CrossNamespaceEdge(
                f"edge src {edge.src!r} does not exist"
            )
        if dst_node is None:
            raise CrossNamespaceEdge(
                f"edge dst {edge.dst!r} does not exist"
            )

        # 4. namespace isolation — edge namespace must match both endpoints
        for endpoint, node in (("src", src_node), ("dst", dst_node)):
            if node.namespace != edge.namespace:
                raise CrossNamespaceEdge(
                    f"edge {endpoint} {node.node_id!r} is in namespace "
                    f"{node.namespace!r}, not {edge.namespace!r}"
                )

        # 5. tier constraint — an edge cannot climb into a more-restricted
        #    tier unless the destination namespace is explicitly shared.
        if ns_config is not None:
            MemoryValidator._check_tier(edge, src_node, dst_node, ns_config)

    @staticmethod
    def _check_tier(
            edge: MemoryEdge,
            src_node: MemoryNode,
            dst_node: MemoryNode,
            ns_config: NamespaceConfig,
    ) -> None:
        src_tier = ns_config.tier
        # The destination tier is only knowable if a config for its namespace
        # is supplied; when absent, assume the same tier as the edge (no leak).
        dst_tier = ns_config.tier
        if src_tier < dst_tier and edge.namespace not in ns_config.shared_with:
            raise CrossNamespaceEdge(
                f"edge {edge.edge_id!r} crosses tiers "
                f"{src_tier.label} -> {dst_tier.label} without sharing"
            )

    # -- graph-level integrity scan ----------------------------------------

    @staticmethod
    def validate_graph(
            nodes: Iterable[MemoryNode],
            edges: Iterable[MemoryEdge],
            ns_configs: Mapping[str, NamespaceConfig] | None = None,
    ) -> ValidationResult:
        """Scan a full graph and collect every integrity issue.

        Mirrors the flat ``validate_ns`` scan but operates on domain objects:
        dangling edges, invalid relations, self-loops, cross-namespace leaks,
        bad confidence, bad (memory_type, node_kind) pairs, tier violations,
        and supersede cycles.
        """
        node_map: dict[NodeId, MemoryNode] = {n.node_id: n for n in nodes}
        edge_list = list(edges)
        issues: list[ValidationIssue] = []

        # -- per-edge checks --
        for edge in edge_list:
            eid = str(edge.edge_id)

            if edge.relation not in EdgeRelation:
                issues.append(ValidationIssue(
                    kind=KIND_INVALID_RELATION, edge_id=eid,
                    detail=f"relation {edge.relation!r} not in EdgeRelation",
                ))
                continue  # further checks are meaningless for a malformed edge

            if edge.src == edge.dst:
                issues.append(ValidationIssue(
                    kind=KIND_SELF_LOOP, edge_id=eid,
                    detail=f"src == dst == {edge.src!r}",
                ))

            src_node = node_map.get(edge.src)
            dst_node = node_map.get(edge.dst)

            if src_node is None or dst_node is None:
                missing = []
                if src_node is None:
                    missing.append(f"src={edge.src!r}")
                if dst_node is None:
                    missing.append(f"dst={edge.dst!r}")
                issues.append(ValidationIssue(
                    kind=KIND_DANGLING_EDGE, edge_id=eid,
                    detail=f"missing endpoints: {', '.join(missing)}",
                ))
                continue  # namespace/tier checks need both nodes

            for label, node in (("src", src_node), ("dst", dst_node)):
                if node.namespace != edge.namespace:
                    issues.append(ValidationIssue(
                        kind=KIND_CROSS_NAMESPACE, edge_id=eid,
                        node_id=str(node.node_id),
                        detail=f"{label} namespace {node.namespace!r} "
                               f"!= edge namespace {edge.namespace!r}",
                    ))

            if ns_configs is not None:
                MemoryValidator._scan_tier(
                    edge, src_node, dst_node, ns_configs, issues,
                )

        # -- per-node checks --
        for node in node_map.values():
            if not (0.0 <= node.confidence <= 1.0):
                issues.append(ValidationIssue(
                    kind=KIND_BAD_CONFIDENCE, node_id=str(node.node_id),
                    detail=f"confidence {node.confidence} out of [0, 1]",
                ))

            try:
                validate_memory_kind(node.memory_type, node.node_kind)
            except InvalidMemoryKind as exc:
                issues.append(ValidationIssue(
                    kind=KIND_BAD_KIND, node_id=str(node.node_id),
                    detail=str(exc),
                ))

        # -- supersede cycle detection --
        issues.extend(
            MemoryValidator._detect_supersede_cycles(node_map)
        )

        return ValidationResult(issues=issues)

    @staticmethod
    def _scan_tier(
            edge: MemoryEdge,
            src_node: MemoryNode,
            dst_node: MemoryNode,
            ns_configs: Mapping[str, NamespaceConfig],
            issues: list[ValidationIssue],
    ) -> None:
        src_cfg = ns_configs.get(src_node.namespace)
        dst_cfg = ns_configs.get(dst_node.namespace)
        if src_cfg is None or dst_cfg is None:
            return
        if src_cfg.tier < dst_cfg.tier and dst_node.namespace not in src_cfg.shared_with:
            issues.append(ValidationIssue(
                kind=KIND_TIER_VIOLATION, edge_id=str(edge.edge_id),
                detail=(
                    f"edge crosses tiers {src_cfg.tier.label} -> "
                    f"{dst_cfg.tier.label} without sharing"
                ),
            ))

    @staticmethod
    def _detect_supersede_cycles(
            node_map: Mapping[NodeId, MemoryNode],
    ) -> list[ValidationIssue]:
        """Find SUPERSEDES chains that form a cycle.

        ``superseded_by`` stores a node-id *string*; resolve it back to a
        ``NodeId`` only when it names a node we hold.  A cycle is detected when
        walking the chain from a starting node revisits a node already seen on
        that walk.
        """
        # Build the supersede adjacency: node_id -> superseded_by NodeId (if known)
        chain: dict[NodeId, NodeId] = {}
        for node in node_map.values():
            if node.superseded_by is None:
                continue
            try:
                target = NodeId.from_string(node.superseded_by)
            except (ValueError, TypeError):
                continue
            if target in node_map:
                chain[node.node_id] = target

        issues: list[ValidationIssue] = []
        for start in chain:
            seen: set[NodeId] = set()
            cur: NodeId | None = start
            while cur is not None and cur in chain and cur not in seen:
                seen.add(cur)
                cur = chain.get(cur)
            if cur is not None and cur in seen:
                issues.append(ValidationIssue(
                    kind=KIND_SUPERSEDE_CYCLE, node_id=str(start),
                    detail=f"supersede cycle detected from {start!r}",
                ))
        return issues
