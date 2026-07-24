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
| [policy-engine.md](policy-engine.md) | `claudenv/domain/policy/policy_engine.py` | ✅ |
| [native-tool-hooks.md](native-tool-hooks.md) | `claudenv/adapters/hooks/policy_hook.py`, `claudenv/adapters/hooks/audit_hook.py` | ✅ |
| [secret-detection.md](secret-detection.md) | `claudenv/domain/security/detectors.py` | ✅ |
| [incident-mode.md](incident-mode.md) | `claudenv/adapters/incident.py` / `claudenv/domain/incident.py` | ✅ |
| [policy-simulation.md](policy-simulation.md) | `claudenv/application/policy/policy_service.py` | ✅ |

## 2. Audit trail — [OVERVIEW.md §2](../OVERVIEW.md#2-no-one-knows-what-the-agent-actually-did)

| Doc | Source | Status |
|---|---|---|
| [audit-ledger.md](audit-ledger.md) | `claudenv/adapters/audit.py`, `claudenv/application/audit/audit_reporting.py`, `claudenv/application/audit/audit_reporting.py` | ✅ |

## 3. Approvals — [OVERVIEW.md §3](../OVERVIEW.md#3-risky-actions-run-without-anyone-checking)

| Doc | Source | Status |
|---|---|---|
| [approvals-workflow.md](approvals-workflow.md) | `claudenv/adapters/approvals_ui.py`, `claudenv/application/approval.py`, `claudenv/adapters/mcp/terminal/server.py` | ✅ |

## 4. Memory — [OVERVIEW.md §4](../OVERVIEW.md#4-the-agent-forgets-everything-every-session)

| Doc | Source | Status |
|---|---|---|
| [memory-graph.md](memory-graph.md) | `claudenv/domain/memory/service/writer.py`, `claudenv/domain/memory/service/reader.py` | ✅ |
| [session-ingestion.md](session-ingestion.md) | `claudenv/application/memory/session_ingestor.py` | ✅ |
| [memory-consolidation.md](memory-consolidation.md) | `claudenv/domain/memory/service/maintenance.py`, `claudenv/domain/memory/service/maintenance.py` | ✅ |
| [memory-sync.md](memory-sync.md) | `claudenv/domain/memory/service/maintenance.py` | ✅ |

## 5. Knowledge (RAG) — [OVERVIEW.md §5](../OVERVIEW.md#5-knowledge-and-search-shouldnt-leave-the-building)

| Doc | Source | Status |
|---|---|---|
| [rag-pipeline.md](rag-pipeline.md) | `claudenv/domain/rag/config.py`, `claudenv/domain/rag_chunker/`, `claudenv/adapters/embedding/`, `claudenv/adapters/vector/lancedb/vector_store.py`, `claudenv/application/rag/git_sync.py` | ✅ |
| [retrieval-poison-screening.md](retrieval-poison-screening.md) | `claudenv/application/rag/service.py` | ✅ |
| [mcp-servers.md](mcp-servers.md) | `claudenv/adapters/mcp/*/server.py` (all six) | ✅ |

## 6. Agents & orchestration — [OVERVIEW.md §6](../OVERVIEW.md#6-one-generalist-agent-doing-everything-badly)

| Doc | Source | Status |
|---|---|---|
| [specialist-agents.md](specialist-agents.md) | `claudenv/_data/templates/repo-onboarding/.claude/agents/` (11 native agents) | ✅ (supersedes `agent_registry.yaml`) |
| [task-routing.md](task-routing.md) | orchestrator agent prompt — model routes natively | ✅ (supersedes `task_router.py`, `agent_handoff.py`) |
| [conflict-resolution.md](conflict-resolution.md) | orchestrator agent prompt — synthesizes conflicts | ✅ (supersedes `conflict_resolver.py`) |

## 7. Operations — [OVERVIEW.md §7](../OVERVIEW.md#7-you-cant-tell-if-its-working-well-or-costing-too-much)

| Doc | Source | Status |
|---|---|---|
| [observability-budgets.md](observability-budgets.md) | `claudenv/application/observability/budget_service.py`, `claudenv/application/observability/feedback_service.py`, `claudenv/application/observability/dashboard_service.py` | ✅ |
| [validation-suite.md](validation-suite.md) | `claudenv/cli/validate.py` | ✅ |

## 8. Onboarding — [OVERVIEW.md §8](../OVERVIEW.md#8-how-this-actually-gets-turned-on)

| Doc | Source | Status |
|---|---|---|
| [onboarding.md](onboarding.md) | `claude-env onboard`, `claudenv/_data/templates/repo-onboarding/` | ✅ |

## 9. CLI Reference — every command, flag, and exit code

| Doc | Source | Status |
|---|---|---|
| [cli-reference.md](../cli-reference.md) | `claudenv/cli/` | ✅ |

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
