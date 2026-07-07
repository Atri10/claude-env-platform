# Design: `docs/guide/` — per-feature technical deep-dive doc set

**Date:** 2026-07-07
**Status:** Approved
**Depends on:** [`2026-07-07-semi-technical-overview-doc-design.md`](2026-07-07-semi-technical-overview-doc-design.md)
(`docs/OVERVIEW.md`) — this doc set is the technical layer `OVERVIEW.md` already
points to under "What's Next."

## Purpose

`docs/OVERVIEW.md` is deliberately semi-technical — no config schemas, no code. This
doc set is the opposite: one highly technical, code-grounded deep dive per
independent feature, covering every configuration parameter, the actual decision
logic (with inline code excerpts), and diagrams of the real internal flow. Audience is
engineers integrating with, extending, or auditing claude-env — people who need to
know exactly how a parameter changes behavior or exactly what a function does, not why
the feature exists at a product level (that's `OVERVIEW.md`'s job).

## Scope: one doc per independent feature, ~18-20 docs

Full inventory, derived from `OVERVIEW.md`'s 7 problem groups (doc boundary = one
source module per doc, no content duplicated across docs — cross-link instead):

**Access control (OVERVIEW §1):** policy-engine, native-tool-hooks, secret-detection,
incident-mode, policy-simulation
**Audit (OVERVIEW §2):** audit-ledger (session-replay and compliance-report may fold
into this doc or split later, TBD at write time based on actual size)
**Approvals (OVERVIEW §3):** approvals-workflow
**Memory (OVERVIEW §4):** memory-graph, session-ingestion, memory-consolidation,
memory-sync
**Knowledge (OVERVIEW §5):** rag-pipeline, retrieval-poison-screening, mcp-servers
**Agents (OVERVIEW §6):** specialist-agents, task-routing, conflict-resolution
**Operations (OVERVIEW §7):** observability-budgets, validation-suite
**Onboarding (OVERVIEW §8):** onboarding

Each doc maps to one source module (e.g. `policy-engine.md` ↔
`security/policy_engine.py` only — Bash command parsing and control-plane guard logic,
though it calls into the policy engine, lives in `hooks/policy_hook.py` and is
documented in `native-tool-hooks.md` instead, cross-linked).

## Folder structure

```
docs/
  OVERVIEW.md                        (existing, unchanged)
  guide/
    README.md                        index, grouped by OVERVIEW's 7 problems
    policy-engine.md                 flagship — built in this phase
    native-tool-hooks.md             \
    secret-detection.md               |
    incident-mode.md                  | remaining ~17 docs —
    policy-simulation.md              | follow-up batch after
    audit-ledger.md                   | flagship sign-off
    approvals-workflow.md             |
    memory-graph.md                   |
    session-ingestion.md              |
    memory-consolidation.md           |
    memory-sync.md                    |
    rag-pipeline.md                   |
    retrieval-poison-screening.md     |
    mcp-servers.md                    |
    specialist-agents.md              |
    task-routing.md                   |
    conflict-resolution.md            |
    observability-budgets.md          |
    validation-suite.md               |
    onboarding.md                    /
  assets/
    architecture.svg, request-lifecycle.svg   (existing, used by OVERVIEW.md)
    guide/
      policy-engine/                  one subfolder per guide doc
        evaluation-order.svg
        tier-resolution.svg
        ...
      native-tool-hooks/
        ...
```

Diagrams are nested per-doc (`docs/assets/guide/<doc-slug>/*.svg`), not a flat
prefixed list — keeps each doc's assets self-contained and easy to find/delete
together if a doc is restructured.

## Shared doc skeleton (light, not strict)

Every guide doc loosely follows this shape, deviating per feature as needed:

1. **Relatability hook** — one line linking back to the relevant `OVERVIEW.md`
   section, e.g. "Relates to: OVERVIEW.md §1 — the agent could read or touch
   something it shouldn't."
2. **What it does (30-second version)** — plain-language summary, one paragraph.
3. **Configuration reference** — exhaustive parameter table: name, type, default,
   effect. This is the most load-bearing section for this doc set's purpose.
4. **How the logic works** — code-level walkthrough of the actual decision logic,
   with real inline code excerpts (5-15 lines, not full files) each followed by
   prose explanation, plus `path/to/file.py:123` links back to source. Excerpts are
   illustrative, not exhaustive — the file itself is the source of truth, this
   section explains it.
5. **Flow diagrams** — 1-2+ diagrams of the real internal flow (decision trees,
   sequence diagrams, resolution order), styled SVG exports in the same visual
   language as `OVERVIEW.md`'s diagrams (Anthropic Sans font, purple/rust/neutral
   color coding), saved to `docs/assets/guide/<doc-slug>/`.
6. **Edge cases, invariants & facts** — gotchas, surprising implementation details,
   invariants enforced in code (e.g. "deny always wins," "stateless, no caching").
   Prefer real details surfaced from reading the code over generic advice.
7. **Related docs** — cross-links to other guide docs and the relevant `OVERVIEW.md`
   section.

## Flagship doc: `policy-engine.md`

Built fully exhaustive in this phase, from a complete read of
`security/policy_engine.py`, `config/global-policy.yaml`,
`config/repo-policy.template.yaml`, and `tests/test_policy_engine.py` (real
test-derived examples, not invented). Covers:

- Full config parameter tables for both `global-policy.yaml` and
  `repo-policy.template.yaml` (~20 keys total)
- The complete evaluation order in `evaluate_path()` (incident check → path
  normalization → override-deny escape hatch → global deny → repo deny → repo allow →
  tier default), each step with a real code excerpt
- `scan_content()`'s separate content-scanning pass and its "stricter mode wins" rule
- Glob-to-regex translation rules (`_glob_to_regex`) and extension matching
  (`_match_ext`) semantics
- Tier resolution (0-3) as a table, showing what each tier merges in at compile time
- Where this module's responsibility ends (detectors, Bash parsing, and the
  control-plane guard live elsewhere — explicitly called out and cross-linked, not
  duplicated)
- Invariants: deny always wins, stateless/no caching, override-deny does not bypass
  content scanning, tier 3 is the only default-deny (whitelist) tier
- 2-3 diagrams: evaluation-order flowchart, tier resolution table/diagram, and a
  glob-matching illustration

This doc sets the depth/style bar. The remaining ~17 docs are a separate follow-up
batch, produced against this doc as the reference example, after the user reviews
its depth and style.

## Non-goals

- Not rewriting or replacing `OVERVIEW.md` — this doc set is purely additive, linked
  from its "What's Next" section.
- Not producing all ~20 docs in this phase — only the guide index and the
  `policy-engine.md` flagship. Remaining docs follow in a batch once the flagship is
  approved.
- Not inventing examples — all concrete input/output examples are pulled from actual
  test files, not fabricated.
