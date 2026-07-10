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
case (rule of three), guided by where change actually recurs.

> Anti-goal: pattern-driven over-engineering. A factory that only ever makes one
> thing, a strategy with one implementation, or an observer with one listener is
> indirection with no payoff. Delete it and inline.

---

## Decision procedure (run this first)

Before reaching for any pattern:

1. **Name the concrete problem.** Duplication? An axis of change? An
   incompatible boundary? Uncontrolled coupling? Growing `if/switch`? If you
   can't name a specific problem, use no pattern.
2. **Is the second case present now?** Not guessed, not anticipated — actually
   present. If only one case exists, the pattern adds indirection with no
   evidence it's needed.
3. **Try the lightest construct first:**
   - A plain function or closure before a Strategy.
   - A parameter before a Factory.
   - A small interface before an Abstract Factory.
   - A dispatch table (dict/map) before a full Command registry.
4. **If you do apply a pattern, name it** in code or a comment so the next
   reader recognises the intent without reverse-engineering it.
5. **Keep the indirection shallow.** One layer of abstraction usually suffices.
   Two layers require strong justification. Three is almost always wrong.

---

## Creational — decouple *what* is created from *who* uses it

### Factory Method / Simple Factory
- **Problem:** callers shouldn't know or depend on concrete types; construction
  logic is non-trivial or must vary by context.
- **Use when:** you need to vary the created type without changing the caller;
  object construction has real complexity (config lookup, pooling, validation).
- **Not when:** a direct `Constructor(args)` is clear — don't wrap it. The call
  site knowing the concrete type is usually fine.
- **Lighter alternative:** a plain function that returns the configured instance.

### Abstract Factory
- **Problem:** create families of related objects that must stay consistent.
- **Use when:** you have multiple families (e.g., matched light/dark UI widgets,
  test doubles vs. production adapters) that must always be used together.
- **Not when:** there is only one family. Prefer separate factories per type.

### Builder
- **Problem:** constructing a complex object step by step; telescoping
  constructors with 8 parameters, many of them optional.
- **Use when:** many optional fields with invariants to enforce during
  construction; a readable, named-parameter-style construction API matters.
- **Not when:** the object has 2–3 fields — a plain constructor or a dataclass
  is clearer.
- **Lighter alternative:** keyword arguments / named parameters in the
  constructor.

### Singleton
- **⚠ Usually a smell.** It's a global with hidden coupling, test pain, and
  thread-safety risk.
- **Prefer:** one instance created at the composition root and injected
  everywhere it's needed. This makes the dependency explicit and swappable.
- **Legitimate uses:** a truly shared resource (a connection pool, a logger)
  managed by the framework's DI container — not by the class itself.

---

## Structural — compose objects into larger structures

### Adapter
- **Problem:** an incompatible third-party or legacy interface must fit the
  contract your system expects.
- **Use when:** isolating an external dependency behind your own port
  (aligns with Dependency Inversion). The adapter is the seam.
- **Pattern:** `class StripeAdapter(PaymentPort): def charge(...): stripe.charge(...)`
- **Not when:** you control both sides of the interface — redesign it instead.

### Facade
- **Problem:** a complex subsystem exposes too many entry points; callers need a
  narrow, stable surface.
- **Use when:** hiding internal complexity from most consumers; reducing the
  blast radius of subsystem changes.
- **Not when:** callers legitimately need the full API — a Facade that exposes
  everything is just a wrapper with extra indirection.

### Decorator
- **Problem:** add cross-cutting behaviour (logging, caching, retry, rate
  limiting, auth) without modifying the wrapped type or creating a subclass
  explosion.
- **Use when:** behaviours combine orthogonally and independently (cache +
  retry + log); adding behaviour at runtime based on configuration.
- **Example:** HTTP middleware stacks, Python `@functools.wraps`, Go
  `http.Handler` chains.
- **Not when:** the added behaviour is tightly coupled to the wrapped logic —
  just put it in the function.

### Composite
- **Problem:** treat individual objects and trees of them uniformly.
- **Use when:** the structure is genuinely recursive (filesystem, UI tree, AST,
  organisation hierarchy).
- **Not when:** the structure isn't actually recursive. A flat list with a
  Composite wrapper is over-engineering.

### Proxy
- **Problem:** control access to an object — for lazy loading, remote
  delegation, access control, or transparent caching.
- **Use when:** the proxy and the real object share the same interface and the
  substitution is transparent to callers.

---

## Behavioral — decouple *how* things collaborate

### Strategy
- **Problem:** a behaviour/algorithm varies and grows; every new case means
  editing a `switch`/`if-elif` ladder.
- **Use when:** the variation is a real axis of change present in at least two
  cases now.
- **Pattern:** stable interface + multiple implementations, injected from
  outside. The caller doesn't know which strategy it has.
- **Lighter alternative:** a dispatch table (dict mapping key → function) is
  often sufficient without a full interface.
- **Not when:** there is only one algorithm today and no concrete second case.

### Observer / Event / Pub-Sub
- **Problem:** one event source needs to notify multiple interested parties
  without knowing them.
- **Use when:** one-to-many, decoupled reactions; the set of listeners varies at
  runtime; the source must not depend on its consumers.
- **Watch:** ordering surprises; cascade effects (observer triggers observer);
  memory leaks from unregistered listeners.
- **Prefer explicit calls** when there is a single, known consumer — observers
  make the flow hard to trace.

### Command
- **Problem:** reify an action as a first-class object to enable undo/redo,
  queuing, logging, scheduling, or approval workflows.
- **Fits naturally** with this platform's `terminal.run` approval model: the
  command is recorded, held for approval, then executed.
- **Use when:** you need to decouple the point of decision from the point of
  execution in time.

### Template Method
- **Problem:** a fixed algorithm skeleton with variable steps.
- **Use when:** the invariant structure is genuinely fixed and the variation is
  only in specific steps.
- **Prefer Strategy (composition)** in most cases — it avoids locking callers
  into a class hierarchy and makes the variation explicit and testable.

### State
- **Problem:** an object's behaviour changes substantially based on internal
  state; sprawling status conditionals (`if state == A: … elif state == B: …`).
- **Use when:** the state machine has 3+ states with distinct behaviour per
  state; transitions have guards or side effects.
- **Pattern:** one class per state; transitions are methods on the state object
  that return the next state.

### Chain of Responsibility
- **Problem:** pass a request through a sequence of handlers; the sender
  shouldn't know which handler will process it.
- **Use when:** middleware, validation pipelines, permission checks —
  where handlers can short-circuit or pass through.
- **Example:** HTTP middleware stacks, policy engine rule chains.

---

## Architectural patterns (the ones that matter most here)

### Ports & Adapters (Hexagonal) / Clean Architecture
The default target for decoupled, testable systems. Domain core with
dependencies pointing inward; I/O, DB, UI, and frameworks live in adapters at
the edge. The core is testable with pure functions and interfaces; adapters swap
without touching business logic. **This is the presumed baseline** — other
patterns serve it.

### Repository
Abstract persistence behind a collection-like interface so domain logic never
touches a DB driver. `OrderRepository.save(order)` not `db.execute(INSERT …)`.

**Watch:** don't let the repository leak query objects or cursor handles that
re-couple the caller to the storage technology. Return domain objects only.

### Dependency Injection
Supply collaborators from outside (constructor parameter or function argument).
The mechanism that makes Dependency Inversion real. Not a framework requirement
— manual injection at a composition root is fine and often simpler.

---

## Patterns by symptom — quick lookup

| Symptom | Pattern to consider |
|---------|-------------------|
| Growing `if type ==` / `switch on kind` | Strategy or dispatch table |
| Can't test logic without a real DB/HTTP | Ports & Adapters + DI |
| Multiple consumers of one event | Observer/Pub-Sub |
| Complex object construction, many optional fields | Builder |
| Third-party API shape conflicts with your interface | Adapter |
| Cross-cutting behaviour (logging, retry, cache) | Decorator |
| Decouple decision from execution in time | Command |
| Recursive structure (tree, hierarchy) | Composite |
| Many status conditionals on a field | State |
| Sequential handlers that can short-circuit | Chain of Responsibility |
| Shared resource with hidden global state | Inject from composition root (not Singleton) |
