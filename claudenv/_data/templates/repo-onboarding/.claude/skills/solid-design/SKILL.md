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
Apply them when they reduce real coupling — not preemptively. The default is
the simplest design; reach for a principle when a specific pain point arrives.

---

## S — Single Responsibility

**A module has one reason to change.** Group what changes together; separate
what changes for different reasons or different stakeholders.

### Smells
- Class or file named `…Manager`, `…Util`, `…Helper`, `…Service` doing five
  unrelated things.
- You edit the same file for unrelated reasons in the same sprint.
- A function mixes policy (decisions, business rules) with mechanism (I/O,
  formatting, persistence).
- A test requires setting up six unrelated dependencies to test one thing.

### Fix
Split by *reason to change* — ask "who or what causes this to change?" Each
distinct answer is a separate module. Separate pure decision logic from
effectful I/O; the decision function takes data and returns data, the shell
function orchestrates I/O around it.

### Trap
Over-splitting into anemic one-method classes with no cohesion. Cohesion is
the twin goal — things that change together for the same reason belong together.
A `UserValidator` with one method is probably just a function.

### Decision test
> "If I change the email validation rule, what else moves?" — only the
> validator. If the answer is "also the DB schema and the API response shape",
> the module is doing too much.

---

## O — Open/Closed

**Open for extension, closed for modification.** Add behaviour by adding code,
not by editing stable code and risking regressions.

### Smells
- Every new case requires editing the same `switch`/`if-elif` ladder.
- A function that started with 3 cases now has 12, and each edit risks breaking
  the others.
- You add a `type` or `kind` parameter to an existing function to "handle one
  more case."

### Fix
Introduce a stable interface; new behaviour is a new implementation, not a new
branch. Common mechanisms:
- **Strategy:** `interface PaymentProcessor { charge(amount) }` — add
  `StripeProcessor`, `PaypalProcessor` without touching the caller.
- **Registry/dispatch table:** `handlers = {"csv": parse_csv, "json": parse_json}`
  — new formats register themselves; the dispatch loop never changes.
- **Plugin pattern:** new behaviour is loaded from configuration or a registry,
  not hardcoded.

### Trap
Speculative extension points for cases that don't exist. Apply on the *second*
real case, not before. An interface with one implementation is indirection with
no payoff — inline until the second case exists.

### Decision test
> "When the third format/payment/event type arrives, does adding it require
> editing stable code?" — if yes, extract the abstraction now.

---

## L — Liskov Substitution

**Subtypes must be usable anywhere their base type is, without surprises.** A
subtype may not strengthen preconditions (demand more from callers) or weaken
postconditions (promise less).

### Smells
- An override throws `NotImplementedException` or `UnsupportedOperationException`.
- Overridden method returns `null` where the base never does.
- Callers `isinstance`-check or `type`-switch to special-case a subtype.
- A `Square` extends `Rectangle` but breaks `setWidth`/`setHeight` invariants.

### Fix
Rethink the "is-a" claim — it is usually false. Use composition instead, or
split the interface (see ISP). If callers must know the concrete type to use it
safely, the hierarchy is wrong.

### Decision test
> "Can I replace every use of `BaseClass` with `SubClass` and have the program
> behave correctly?" — if no, the subtype relationship is broken.

---

## I — Interface Segregation

**No client should depend on methods it doesn't use.** Prefer many small,
role-focused interfaces over one fat one.

### Smells
- Implementers are forced to stub (`raise NotImplemented`) methods they don't
  support.
- An interface has 12 methods but most consumers only call 2 of them.
- Adding a method to an interface forces changes in 8 unrelated implementers.

### Fix
Split into role interfaces. The consumer owns and names the interface it needs:
`Reader`, `Writer` rather than `ReadWriter`; `Authenticator` rather than
`UserManager`. Concrete types can implement multiple small interfaces.

### Decision test
> "Does every implementer of this interface actually implement all of its
> methods meaningfully?" — if no, split the interface.

---

## D — Dependency Inversion

**High-level policy must not depend on low-level detail. Both depend on an
abstraction owned by the policy side.**

### Smells
- Business logic directly imports a concrete DB driver, HTTP client, email
  library, or ORM class.
- A domain function instantiates its own dependencies (`db = PostgresDB()`
  inside a service function).
- Tests require a running database or network to exercise business logic.
- Changing the persistence layer requires touching business logic files.

### Fix
Define the interface where it is *used* (consumer-owned port), inject the
concrete implementation from the edge (constructor or parameter injection).
Wire concrete choices only in a composition root at the outermost boundary.

```python
# Wrong: business logic depends on concrete
class OrderService:
    def __init__(self):
        self.db = PostgresDB(os.environ["DATABASE_URL"])  # detail leaks in

# Right: business logic depends on abstraction it owns
class OrderRepository(Protocol):
    def save(self, order: Order) -> None: ...
    def find(self, id: str) -> Order | None: ...

class OrderService:
    def __init__(self, repo: OrderRepository):  # injected from outside
        self.repo = repo
```

### Trap
Interfaces for their own sake: an `IEmailSender` that wraps a concrete
`EmailSender` with no second implementation and no test double. Don't create
an interface until you have a reason (testability or a real alternative
implementation).

### Decision test
> "Can I test the business logic without starting a database, HTTP server, or
> external service?" — if no, the dependencies are inverted the wrong way.

---

## Working checklist

Before finishing any design or implementation:

- [ ] Can I state each module's single reason to change in one sentence?
- [ ] Does new behaviour go in via new code, or does it require editing stable code?
- [ ] Are subtypes honestly substitutable without callers special-casing them?
- [ ] Does every implementer of each interface actually use all its methods?
- [ ] Does policy depend on an abstraction it owns, with detail injected from the edge?
- [ ] Can business logic be tested without I/O? (The answer should be yes.)
- [ ] **Is a plain function enough here?** Don't manufacture interfaces for a
      single implementation with no second case in sight. SOLID serves
      simplicity — never complexity for its own sake.

---

## Quick-reference decision tree

```
Feeling pain? Ask:
  → "I keep editing the same class for unrelated reasons"  → S violation → split
  → "Every new case requires editing the same if/switch"   → O violation → Strategy/dispatch
  → "Callers type-check my subclass"                       → L violation → composition
  → "Implementers stub half the interface"                 → I violation → split interface
  → "Tests need a real DB/network to run"                  → D violation → inject abstraction
  → "None of the above, but design feels off"              → read architecture-review skill
```
