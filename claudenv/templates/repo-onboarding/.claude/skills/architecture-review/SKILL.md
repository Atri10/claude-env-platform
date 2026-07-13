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

Zoom out from lines to **structure**. The question is not "is this code
correct" (that is `code-review`) but "does this change keep the system
decoupled, cohesive, and cheap to evolve — or does it add coupling and risk
we will pay for repeatedly?"

Ground it in reality: read module layout and history via the MCP filesystem
and git servers; `lancedb.search` for how components interconnect; and
`memory.recall` for prior architectural decisions so you don't contradict or
silently reverse one.

---

## Before reviewing the design itself

1. **Recall prior decisions.** `memory.recall` for any ADR, design decision,
   or architectural note relevant to this area. Contradicting a prior decision
   needs an explicit reason and a memory update — silence is not a supersession.
2. **Read the existing structure.** Scan the module/package layout. Understand
   the current dependency graph before assessing whether the change improves or
   degrades it.
3. **Understand the stated goal.** What problem is this design solving? What
   constraint is it operating under (performance, privacy tier, team boundary)?
   A good design for the wrong constraint is still wrong.
4. **Check the tier.** Read `.claude/repo-policy.yaml`. Tier-2/3 constraints
   (isolation, approval gates, no external fetch) must be respected in the
   architecture, not worked around.

---

## Dimensions to assess — in order

### 1. Dependency direction (🔴 if violated)
Do dependencies point **inward** (detail → policy, adapter → domain)?

- Business/domain logic must not import a framework, ORM, HTTP client, or
  external service directly.
- A new module that the domain core imports is a potential inversion — flag it.
- Acceptable: domain imports standard library types, pure data structures, and
  domain-owned interfaces. Not acceptable: domain imports `sqlalchemy.orm`,
  `flask`, `requests`, `boto3` directly.
- **Check:** draw the dependency arrow. Does it point from the volatile edge
  toward the stable core, or the other way? If the core now depends on the
  edge, the direction is wrong.

### 2. Boundaries and contracts (🔴 if leaked)
Is the seam in the right place, and is it crossed through an explicit contract?

- Crossing a boundary by importing an internal type or accessing a private field
  is a leaked boundary — the boundary no longer exists in any meaningful sense.
- The contract at a boundary should express *what*, not *how*. An interface
  returning a `*sql.Rows` cursor has leaked the implementation.
- **Check:** for each boundary crossing in the change, is there a named
  interface or message type that hides the implementation? Or is implementation
  detail flowing across?

### 3. Coupling and cohesion (🟡)
Does the change raise coupling or reduce cohesion?

- **New coupling risks:** shared mutable state added between modules; a new
  field on a widely-shared struct/class; cross-module knowledge encoded in
  string constants or magic numbers.
- **Cohesion breaks:** logic that belongs together is now split across files for
  no structural reason; an existing cohesive module is being pulled apart by
  this change.
- **Check:** if the most likely next requirement lands, how many modules move?
  A good boundary localises change. A bad one spreads it.

### 4. Dependency cycles (🔴 — hard stop)
Any new cycle between modules, packages, or services?

- Module A imports B which imports A is a cycle — a hard stop. Cycles prevent
  independent compilation, testing, and deployment.
- **Fix:** identify which direction the dependency should point; extract a shared
  abstraction both sides depend on; or merge the modules if they are actually
  one cohesion unit.
- **Check:** after the change, is there any new path from module X back to
  itself through the import graph?

### 5. Blast radius and change cost (🟡)
How many components move when the likely next requirement arrives?

- Identify the 2–3 most likely future changes to this area (new feature, new
  format, new provider). For each: how many files change?
- If the answer is "many files across many modules", the seam is in the wrong
  place.
- **Check:** a good boundary means the next change touches 1–2 files. A bad one
  means 8+.

### 6. Consistency with established patterns (🟡)
Does the change fit the repo's established architecture?

- If the repo uses hexagonal/ports-and-adapters, a new module that mixes domain
  logic with persistence is a consistency violation — not just a style issue.
- Divergence from established patterns needs an explicit, recorded reason
  (an ADR). Silent divergence is a maintenance cost that compounds.
- **Check:** does this module follow the same file layout, naming, and boundary
  style as its neighbours?

### 7. Cross-cutting concerns (🟡)
Auth, logging, error handling, transactions, audit — applied consistently?

- Are new error paths handled at the right layer (not swallowed in the middle)?
- Is logging/tracing applied at the boundary (entry/exit) rather than scattered
  through business logic?
- If the repo has an audit requirement (it does, via the claude-env ledger), does
  the new component route through the audited path, or create an ungoverned
  side channel?

### 8. Governance fit (🔴 if violated)
Does the design respect the platform's privacy tier, least-privilege model, and
audit path?

- Any new outbound network call must be gated by tier (no external fetch on
  tier 2–3).
- Any new file I/O must go through the `filesystem-policy` MCP server, not a
  raw filesystem call.
- Any new terminal command must go through `terminal.run` (approval gated).
- A design that routes around governance is not a valid design — it is a
  security finding.

---

## Output format

For each structural finding:

```
🔴/🟡/🟢 [dimension] module or file
What: <one sentence — the structural problem>
Future cost: <the specific change that becomes expensive if this is not fixed>
Fix: <the boundary move, inversion, or extraction that resolves it>
```

End with:
- **ADR prompt:** if this change encodes a real decision (technology choice,
  new boundary, pattern adoption), it should be captured as an ADR. Write a
  draft or prompt the `architect` agent.
- **Memory update:** if a prior decision is being reversed or superseded, update
  the memory node explicitly.
- **Verdict:** sound / sound-with-changes / redesign-recommended, plus the
  single most important structural concern.

---

## Discipline

- **Judge against real constraints,** not an abstract ideal. The right
  architecture for a solo privacy-tier-3 repo is not the same as for a
  public-tier-0 library.
- **Prefer the smallest structural change** that removes the coupling. Don't
  recommend a full hexagonal rewrite when moving one import suffices.
- **Simplicity counts as architecture.** Flag speculative layering, unused
  extension points, and premature abstraction as over-engineering — they are
  structural debt too.
- **Never approve a design that routes around platform governance,** regardless
  of how reasonable the underlying goal is.
