# Design: `docs/OVERVIEW.md` — semi-technical product overview

**Date:** 2026-07-07
**Status:** Approved (revised — see Revision below)

## Revision: problem-first restructure

After the first pass (module-by-module, 21 sections), the user asked for a structure
optimized for lower-context human readers: group the same coverage into ~5-7 broad,
recognizable problems, each naming the feature(s) that solve it and walking through
one concrete before/after example — rather than one section per module.

Final grouping (7 problems, full feature coverage preserved):

1. The agent could read or touch something it shouldn't → policy engine, hooks,
   secret/injection detection, incident mode, policy simulation
2. No one knows what the agent actually did → audit ledger, session replay,
   compliance reporting
3. Risky actions run without anyone checking → approvals workflow, terminal
   allow-listing
4. The agent forgets everything, every session → memory graph, session ingestion,
   consolidation/pruning, team knowledge sync
5. Knowledge and search shouldn't leave the building → local RAG, retrieval/poison
   screening, the six MCP servers as the local-only enforcement surface
6. One generalist agent doing everything, badly → specialist agents, task
   routing/handoff, conflict resolution
7. You can't tell if it's working well or costing too much → budgets/cost tracking,
   feedback loop, dashboard, validation suite, nightly automation

Onboarding remains a short closing "how this gets turned on" section. The
architecture diagram moved to a shared intro above all seven problems (it's the
answer to all of them, not any one). The request-lifecycle diagram moved into problem
3, where it's the most direct illustration.

## Purpose

claude-env's only current documentation is `README.md` — a 1013-line exhaustive
technical reference (installation, CLI, YAML schemas, appendices) — and `CLAUDE.md`,
which governs platform *development*, not product understanding. Neither is suited to
a semi-technical audience deciding whether to adopt the platform: someone who
understands "policy engine" and "audit log" conceptually but doesn't want CLI flags or
YAML schemas on first read.

`docs/OVERVIEW.md` fills that gap: a single narrative document explaining the problem
claude-env solves, how it solves it, and — per explicit user instruction — every
feature area in the repository, each with a concrete use case. Deep technical
references (CLI flags, config schemas) stay in `README.md` and a future `docs/guide/`;
this doc links out to them rather than duplicating them.

## Audience

Prospective adopters: engineering leads or teams evaluating whether to bring
claude-env into their org or repo. Semi-technical — comfortable with architecture
concepts, not necessarily with the codebase.

## Scope: full coverage, uniform depth

The user explicitly asked that this document cover *everything* in the repository,
with each feature area getting its own treatment rather than being folded into a
condensed table. Every module identified in the repo tour gets a section using the
same three-part shape:

1. **Problem** — what breaks without this feature (a concrete failure scenario)
2. **Solution** — plain-English mechanism, with one moderate technical callout
   (e.g. the tier matrix, the hash-chain concept) and a light "implemented in
   `module.py`" pointer for credibility
3. **Use cases** — 2-3 concrete scenarios where this feature matters

## Document outline

**Part I — The Problem**
Scenario-driven framing of what goes wrong when an AI coding agent has unrestricted
access to a repo: unrestricted file access, no audit trail, secret/PII leakage into
prompts or a RAG index, unreviewed destructive actions, prompt injection via retrieved
documents.

**Part II — Architecture at a Glance**
One diagram (reusing the existing `docs/architecture.svg`, which already matches the
intended visual style) showing the governance chokepoint: Claude Code reaches the
machine only through MCP servers and native-tool hooks, both of which consult one
policy engine and write to one audit ledger.

**Part III — Security & Governance** (7 sections)
1. Policy-Enforced File Access (tier matrix: 0 public → 3 restricted, deny always wins)
2. Tamper-Evident Audit Ledger (hash-chain concept, `verify_chain()`)
3. Native-Tool Hooks (PreToolUse/PostToolUse governing Read/Write/Edit/Bash)
4. Secret & Prompt-Injection Detection (`security/detectors.py`)
5. Incident Mode (kill switch, fail-closed)
6. Policy Simulation (dry-run + drift diff before rollout)
7. Approvals Workflow (human-in-the-loop gate, blocks pending decision)

A second diagram goes here: a request-lifecycle flow (agent action → hook →
policy engine → allow / deny / approval-gate → audit ledger), in the same visual
style as `architecture.svg`, exported as `docs/assets/request-flow.svg`.

**Part IV — Knowledge: RAG & Memory** (6 sections)
8. Local RAG (AST-aware chunking, pluggable local embeddings, optional rerank, LanceDB)
9. Retrieval Pipeline & Poison Screening (retrieved text treated as data, not instructions)
10. Memory Graph (episodic / semantic / procedural / agent nodes+edges, confidence decay)
11. Memory Consolidation & Pruning (nightly merge of low-confidence clusters, archiving)
12. Session Ingestion (Claude Code transcripts → episodic memory, secret-redacted)
13. Team Knowledge Sync (namespace-isolated export/import)

**Part V — Agents & Orchestration** (3 sections)
14. Specialist Agents (11 roles, explicit tool/write scopes via `agent_registry.yaml`)
15. Task Routing & Handoff (decompose → route → scoped context package)
16. Conflict Resolution (reconciling specialist output, security veto wins)

**Part VI — MCP Servers**
The six local stdio servers as the enforcement surface: filesystem-policy (chokepoint),
git (read-only), lancedb-rag, memory-graph, terminal (approval-gated), documentation
(tiered external fetch — the only network egress).

**Part VII — Observability & Operations** (5 sections)
17. Metrics, Cost Tracking & Budgets (token/cost estimation, per-repo monthly caps)
18. Feedback Loop (retrieval-quality signal from edits, reranking boost)
19. Dashboard (local read-only Datasette UI)
20. Validation Suite (installation / security / memory / agents / MCP / end-to-end checks)
21. Nightly Automation & Backup (systemd/launchd jobs, memory maintenance, re-indexing)

**Part VIII — Onboarding a Repository**
What `claude-env onboard` installs into a target repo: generated policy, RAG index,
memory namespace, and the `templates/repo-onboarding/` deliverable (CLAUDE.md +
skills + read-only reviewer subagents).

**Part IX — What's Next**
Pointer to `README.md` for the exhaustive technical reference (CLI flags, YAML
schemas, installation steps) and to a future `docs/guide/` for per-module deep dives.

## Diagrams

Two diagrams, exported as styled SVG files under `docs/assets/` (not Mermaid — the
user wants custom visual styling, not default renderer boxes) and embedded via
standard Markdown image links:

1. **Architecture at a glance** — reuse existing `docs/architecture.svg` as-is; it
   already matches the intended visual language (Anthropic Sans font, color-coded
   layers: client, MCP servers, native hooks, security spine, local stores).
2. **Request lifecycle flow** — new diagram, same visual style, showing a single
   action's path from agent intent through enforcement to the ledger, including the
   allow/deny/approval-gate branch.

## Non-goals

- Not replacing `README.md` — that remains the exhaustive technical reference.
- Not building out `docs/guide/` deep-dive files yet — `docs/OVERVIEW.md` only points
  at where they'll go.
- Not documenting CLI flags, YAML schemas, or installation steps in detail — link to
  `README.md` sections instead.
