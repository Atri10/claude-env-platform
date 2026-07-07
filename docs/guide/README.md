# Technical Guide

Code-level deep dives — one doc per independent feature. Each covers the full
configuration surface, the actual decision logic (with inline code excerpts and
`file.py:line` references), diagrams of the real internal flow, and edge
cases/invariants pulled directly from the source and tests.

For the product-level pitch (problem → feature → use case, no code), start with
[`../OVERVIEW.md`](../OVERVIEW.md) instead — every section below links back to the
`OVERVIEW.md` problem it belongs to.

Doc boundary: **one doc per source module.** If module A calls into module B, A's doc
cross-links to B's rather than re-explaining B's logic.

Status legend: ✅ written · 🔜 planned (not yet written)

---

## 1. Access control — [OVERVIEW.md §1](../OVERVIEW.md#1-the-agent-could-read-or-touch-something-it-shouldnt)

| Doc | Source | Status |
|---|---|---|
| [policy-engine.md](policy-engine.md) | `security/policy_engine.py` | ✅ |
| native-tool-hooks.md | `hooks/policy_hook.py`, `hooks/audit_hook.py` | 🔜 |
| secret-detection.md | `security/detectors.py` | 🔜 |
| incident-mode.md | `security/incident.py` | 🔜 |
| policy-simulation.md | `security/policy_sim.py` | 🔜 |

## 2. Audit trail — [OVERVIEW.md §2](../OVERVIEW.md#2-no-one-knows-what-the-agent-actually-did)

| Doc | Source | Status |
|---|---|---|
| audit-ledger.md | `audit/audit_logger.py`, `audit/session_replay.py`, `audit/compliance_report.py` | 🔜 |

## 3. Approvals — [OVERVIEW.md §3](../OVERVIEW.md#3-risky-actions-run-without-anyone-checking)

| Doc | Source | Status |
|---|---|---|
| approvals-workflow.md | `agents/orchestration/approvals_ui.py`, `agents/orchestration/approval_gate.py`, `mcp-servers/terminal/server.py` | 🔜 |

## 4. Memory — [OVERVIEW.md §4](../OVERVIEW.md#4-the-agent-forgets-everything-every-session)

| Doc | Source | Status |
|---|---|---|
| memory-graph.md | `memory/memory_manager.py`, `memory/memory_retriever.py` | 🔜 |
| session-ingestion.md | `memory/session_ingestor.py` | 🔜 |
| memory-consolidation.md | `memory/memory_consolidator.py`, `memory/memory_pruner.py` | 🔜 |
| memory-sync.md | `memory/memory_sync.py` | 🔜 |

## 5. Knowledge (RAG) — [OVERVIEW.md §5](../OVERVIEW.md#5-knowledge-and-search-shouldnt-leave-the-building)

| Doc | Source | Status |
|---|---|---|
| rag-pipeline.md | `rag/config.py`, `rag/chunkers/`, `rag/embeddings/`, `rag/rerankers/`, `rag/retrievers/lance_store.py` | 🔜 |
| retrieval-poison-screening.md | `rag/pipelines/retrieve.py` | 🔜 |
| mcp-servers.md | `mcp-servers/*/server.py` (all six) | 🔜 |

## 6. Agents & orchestration — [OVERVIEW.md §6](../OVERVIEW.md#6-one-generalist-agent-doing-everything-badly)

| Doc | Source | Status |
|---|---|---|
| specialist-agents.md | `agents/agent_registry.yaml` | 🔜 |
| task-routing.md | `agents/orchestration/task_router.py`, `agents/orchestration/agent_handoff.py` | 🔜 |
| conflict-resolution.md | `agents/orchestration/conflict_resolver.py` | 🔜 |

## 7. Operations — [OVERVIEW.md §7](../OVERVIEW.md#7-you-cant-tell-if-its-working-well-or-costing-too-much)

| Doc | Source | Status |
|---|---|---|
| observability-budgets.md | `observability/budgets.py`, `observability/feedback.py`, `observability/dashboard.py` | 🔜 |
| validation-suite.md | `validation/*.py` | 🔜 |

## 8. Onboarding — [OVERVIEW.md §8](../OVERVIEW.md#8-how-this-actually-gets-turned-on)

| Doc | Source | Status |
|---|---|---|
| onboarding.md | `scripts/register_repo.py`, `templates/repo-onboarding/` | 🔜 |

---

## Shared format

Every doc loosely follows this shape (deviating per feature where it doesn't fit):

1. **Relatability hook** — one line back to the relevant `OVERVIEW.md` section.
2. **What it does (30-second version)** — plain-language summary.
3. **Configuration reference** — every parameter: name, type, default, effect.
4. **How the logic works** — real inline code excerpts + `file.py:line` links,
   walking the actual decision logic.
5. **Flow diagrams** — 1-2+ styled SVGs under `../assets/guide/<doc-slug>/`.
6. **Facts, invariants & edge cases** — gotchas and surprising details pulled from
   the source and tests, not generic advice.
7. **Related docs** — cross-links.

See [`policy-engine.md`](policy-engine.md) for the reference example.
