---
name: design-patterns
description: >-
  Choose (and reject) design patterns deliberately. For each pattern: the
  problem it solves, when to reach for it, when NOT to, and the lighter
  alternative. Use when a design has real duplication or a real axis of change
  to decouple — not to decorate simple code with structure.
---

# Design Patterns — Selection, Not Decoration

A pattern is a named solution to a **recurring** problem. Its value is
communication and decoupling; its cost is indirection. **Only introduce a
pattern when the problem it solves is actually present.** The default is the
plainest code that works. Reach for a pattern on the *second or third* concrete
case (rule of three), guided by where change recurs.

> Anti-goal: pattern-driven over-engineering. A factory that only ever makes one
> thing, a strategy with one implementation, or an observer with one listener is
> indirection with no payoff. Delete it and inline.

## Creational — decouple *what* is created from *who* uses it

- **Factory Method / Simple Factory** — centralize object creation when
  construction logic is non-trivial or must vary. *Use when:* callers shouldn't
  know concrete types. *Not when:* a direct constructor is clear — don't wrap it.
- **Abstract Factory** — create families of related objects that must stay
  consistent (e.g., matched UI widgets). *Not when:* there's only one family.
- **Builder** — assemble a complex object step by step; kills telescoping
  constructors and boolean-parameter soup. *Use when:* many optional fields /
  invariants to enforce during construction.
- **Singleton** — ⚠️ usually a smell. It's a global with hidden coupling and
  test pain. *Prefer:* one instance created at the composition root and injected.

## Structural — compose objects into larger structures

- **Adapter** — make an incompatible interface fit the one you need; the classic
  seam for wrapping a third-party/legacy API behind your own port. *Use when:*
  isolating an external dependency (aligns with dependency inversion).
- **Facade** — a simple front over a complex subsystem. *Use when:* callers need
  a narrow, stable entry point.
- **Decorator** — add behavior (logging, caching, retry, auth) without changing
  the wrapped type; composition instead of subclass explosion. *Use when:*
  behaviors combine orthogonally.
- **Composite** — treat individual objects and trees of them uniformly (files/
  folders, UI nodes). *Not when:* the structure isn't actually recursive.
- **Proxy** — stand-in controlling access (lazy load, remote, access control).

## Behavioral — decouple *how* things collaborate

- **Strategy** — swap an algorithm/policy behind a stable interface; the primary
  cure for growing `if/switch` ladders (Open/Closed). *Use when:* the variation
  is a real axis of change.
- **Observer / Pub-Sub** — notify interested parties without the source knowing
  them. *Use when:* one-to-many, decoupled reactions. *Watch:* ordering and
  cascade surprises; prefer explicit calls when there's a single consumer.
- **Command** — reify an action as an object (undo/redo, queues, audit). Fits
  naturally with this platform's approval/audit model.
- **Template Method** — fix the skeleton, let subclasses fill steps. *Prefer
  Strategy (composition)* unless the skeleton is genuinely invariant.
- **State** — behavior changes with internal state; replaces sprawling status
  conditionals with per-state objects.
- **Chain of Responsibility** — pass a request along handlers until one takes it
  (middleware, validation pipelines).

## Architectural (the ones that matter most here)

- **Ports & Adapters (Hexagonal) / Clean Architecture** — domain core with
  dependencies pointing inward; I/O, DB, UI, and frameworks live in adapters at
  the edge. This is the default target for decoupled, testable systems.
- **Repository** — abstract persistence behind a collection-like interface so
  domain logic never touches the DB driver. *Watch:* don't let it leak query
  objects that re-couple you to the store.
- **Dependency Injection** — supply collaborators from outside (constructor/
  parameter). The mechanism that makes dependency inversion real.

## Decision procedure

1. Name the concrete problem: duplication? an axis of change? an incompatible
   boundary? uncontrolled coupling? If you can't name it, **use no pattern**.
2. Is there a second real case now, or only a guess about the future? If a
   guess, wait.
3. Pick the *lightest* construct that solves it — often a plain function, a
   parameter, or a small interface beats a named pattern.
4. If you do apply one, name it in code/comments so the next reader recognizes
   the intent, and keep the indirection shallow.
