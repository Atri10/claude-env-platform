---
name: architecture-review
description: >-
  Review a design, module boundary, or dependency change at the system level:
  coupling, cohesion, dependency direction, blast radius, and long-term
  changeability. Use when a change moves boundaries, adds a dependency,
  introduces a new component/service, or when evaluating an ADR or a proposed
  design before implementation.
---

# Architecture Review

Zoom out from lines to **structure**. The question is not "is this code correct"
(that's `code-review`) but "does this change keep the system decoupled,
cohesive, and cheap to evolve — or does it add coupling and risk we'll pay for
repeatedly?"

Ground it in reality: read module layout and history via the filesystem-policy
and git MCP servers, `lancedb.search` for how components interconnect, and
`memory.recall` for prior architectural decisions so you don't contradict or
silently reverse one.

## Dimensions to assess

1. **Dependency direction** — Do dependencies point inward (detail → policy),
   or does business logic now depend on a framework/DB/UI/external service?
   Flag any inward-facing core reaching outward.
2. **Boundaries & contracts** — Is the seam in the right place? Is it crossed
   through an explicit contract, or by reaching into internals? Does this change
   leak an implementation detail across a boundary?
3. **Coupling & cohesion** — Does the change raise coupling (more things must
   change together, new shared mutable state, new cross-module knowledge)? Does
   it split cohesive logic or merge unrelated concerns?
4. **Cycles** — Any new dependency cycle between modules/packages/services?
   Cycles are a hard stop — propose the inversion or extraction that breaks them.
5. **Blast radius & change cost** — If the likely next requirement lands, how
   many components move? A good boundary localizes change; a bad one spreads it.
6. **Consistency** — Does it fit the established architecture (e.g., ports &
   adapters, repository pattern), or introduce a competing style? Divergence
   needs an explicit, recorded reason.
7. **Cross-cutting concerns** — Auth, logging, error handling, transactions,
   audit: applied consistently at the right layer, not scattered ad hoc.
8. **Governance fit** — Does the design respect the repo's privacy tier,
   least-privilege, and audit model? Does any component try to bypass the
   platform MCP servers or introduce ungoverned I/O?

## Output

- **Structural findings**, ranked: 🔴 will-cause-pain (cycles, inverted deps,
  leaked boundaries) · 🟡 should-address (rising coupling, misplaced seam) ·
  🟢 note (consistency/naming at the architectural level).
- For each: the coupling/risk it creates, the *future change* it makes expensive,
  and the boundary/inversion/extraction that fixes it.
- **ADR prompt:** if the change encodes a real decision (technology choice, new
  boundary, pattern adoption), recommend capturing it as an ADR with context,
  options considered, and consequences — and write the decision to `memory`.
- **Verdict:** sound / sound-with-changes / redesign-recommended, plus the single
  most important structural concern.

## Discipline

- Judge against constraints that actually exist here (local-first, privacy tiers,
  the established patterns in this repo) — not against a generic ideal.
- Prefer the smallest structural change that removes the coupling; don't
  recommend a rewrite when a boundary move suffices.
- Simplicity counts as architecture: flag speculative layering and unused
  extension points as over-engineering.
