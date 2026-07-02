---
name: solid-design
description: >-
  Apply the SOLID principles concretely — with the smell that signals each
  violation and the refactoring that fixes it. Use when designing classes,
  modules, or interfaces; when a change feels like it's touching too much; or
  during review to name a coupling/cohesion problem precisely.
---

# SOLID, Applied

SOLID is a lens for **decoupling and cohesion**, not a checklist to satisfy
mechanically. Each principle earns its keep by making one axis of change cheap.
Apply them when they reduce real coupling — not preemptively.

## S — Single Responsibility

**A module has one reason to change.** Group what changes together; separate what
changes for different reasons or different stakeholders.

- **Smell:** a class named `...Manager`/`...Util`/`...Helper`; a file you edit
  for unrelated reasons; a function mixing policy (decisions) with mechanism
  (I/O, formatting).
- **Fix:** split by reason-to-change. Separate the pure decision from the effect.
- **Trap:** over-splitting into anemic one-method classes. Cohesion is the twin
  goal — things that change together belong together.

## O — Open/Closed

**Open for extension, closed for modification.** Add behavior by adding code, not
by editing stable code and risking regressions.

- **Smell:** every new case means editing the same `switch`/`if-elif` ladder.
- **Fix:** polymorphism / strategy — a stable interface, new implementations
  plugged in. A registry or dispatch table.
- **Trap:** speculative extension points nobody uses. Apply on the *second* case,
  guided by where change actually recurs — not day one.

## L — Liskov Substitution

**Subtypes must be usable anywhere their base type is, without surprises.** A
subtype may not strengthen preconditions or weaken postconditions.

- **Smell:** an override that throws `NotSupported`, returns null where the base
  never does, or callers that `isinstance`-check to special-case a subtype.
- **Fix:** rethink the hierarchy — the "is-a" is false. Prefer composition, or
  split the interface (see ISP).
- **Classic tell:** Square-extends-Rectangle. If overriding breaks the base's
  contract, it isn't a subtype.

## I — Interface Segregation

**No client should depend on methods it doesn't use.** Prefer many small,
role-focused interfaces over one fat one.

- **Smell:** implementers forced to stub methods they don't support; an interface
  that only some callers use half of.
- **Fix:** split into role interfaces (`Reader`, `Writer` rather than `Store`).
  Consumers depend only on the slice they need.

## D — Dependency Inversion

**Depend on abstractions, not concretions; high-level policy must not depend on
low-level detail.** Both depend on an interface owned by the policy side.

- **Smell:** business logic that `import`s a concrete DB driver, HTTP client, or
  framework class; `new`-ing dependencies inside logic instead of receiving them.
- **Fix:** define the interface where it's *used* (consumer-owned), inject the
  implementation from the edge (constructor/parameter injection). Wire concrete
  choices only in a composition root at the boundary.
- **Payoff:** the core becomes testable with fakes, and edges (DB, API, UI) swap
  without touching logic.

## Working checklist

- Can I state each module's single reason to change?
- Does new behavior go in via new code, or by editing stable code?
- Are subtypes honestly substitutable, or do callers special-case them?
- Do consumers depend only on the methods they actually call?
- Does policy depend on an interface it owns, with detail injected from the edge?
- **Is a plain function enough here?** Don't manufacture interfaces for a single
  implementation with no second case in sight. SOLID serves simplicity.
