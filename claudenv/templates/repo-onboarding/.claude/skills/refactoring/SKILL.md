---
name: refactoring
description: >-
  Safe refactoring procedure — characterisation tests first, small behaviour-
  preserving steps, the Martin Fowler catalog applied concretely. Use when
  improving existing code without changing its observable behaviour: cleaning
  up a god class, extracting a module, inverting a dependency, or preparing
  code for a new feature.
---

# Refactoring

Refactoring means **changing code structure without changing observable
behaviour**. The two activities — refactoring and adding features — must be
strictly separated. Mixing them makes both harder: you can't tell if a bug was
introduced by the refactor or the feature, and the diff becomes unreadable.

> "First make the change easy (this may be hard), then make the easy change."
> — Kent Beck

This is a claude-env-governed repository. All file writes go through the MCP
filesystem server. Run tests via `terminal.run_tests` after every step. An
approval gate opens for state-mutating terminal commands.

---

## The safety protocol

### 1. Stop: do you have a passing test suite?

Before refactoring anything, run `terminal.run_tests`. If the suite is not
green, you have a bug, not a refactoring opportunity. Fix the bug first (see
the `debugging` skill), then refactor.

A failing test suite means you have no baseline to compare against — you cannot
know whether your refactor preserved behaviour.

### 2. Write characterisation tests (if tests are missing)

If the code you are about to refactor has no tests:
1. **Read the code and understand its current behaviour** — including any bugs.
2. **Write tests that capture the current behaviour**, not the desired
   behaviour. These tests will be green against the existing code (even if the
   behaviour is wrong). They are your safety net, not your specification.
3. Run the suite — confirm the characterisation tests pass.
4. Now refactor. The tests will catch any unintended behaviour change.
5. Separately write tests for the *correct* behaviour. If they expose bugs,
   fix the bugs as a separate commit from the refactor.

### 3. Work in the smallest possible steps

The cardinal rule: **after every change, the test suite must be green.**
Not "green after the next three changes" — green after each one. If you ever
have a red suite, the last change introduced a regression — undo it immediately.

Small steps make it easy to identify where something went wrong. Large steps
produce large diffs where bugs hide.

### 4. Commit often, on green

Commit after each logical refactoring step (extract function, rename,
move module). A commit history of small, green steps is easy to review and
bisect. A single giant commit is neither.

---

## The refactoring catalog — applied

### Extract Function
**When:** a piece of code can be named clearly and serves one purpose within
a larger function. Comments that say "// do X" are a signal to extract.

```python
# Before
def process_order(order):
    # validate order items
    for item in order.items:
        if item.quantity <= 0:
            raise ValueError(f"Invalid quantity for {item.sku}")
    # calculate total
    total = sum(item.price * item.quantity for item in order.items)
    return total

# After
def _validate_items(items):
    for item in items:
        if item.quantity <= 0:
            raise ValueError(f"Invalid quantity for {item.sku}")

def _calculate_total(items):
    return sum(item.price * item.quantity for item in items)

def process_order(order):
    _validate_items(order.items)
    return _calculate_total(order.items)
```

**Safety:** extract first, verify green, then move.

### Rename
**When:** the name does not reflect the current purpose, or the purpose has
drifted from the original name.

- Rename in one step using IDE/search-replace; don't leave a mix of old and new names.
- For a public API name (function, method, field): add the new name first, deprecate
  the old one, migrate callers, then remove. Never rename a public API in one step.
- Run tests after every rename. A missed call site will produce a clear error.

### Move Function/Class
**When:** a function or class is in the wrong module — it depends on or is used
primarily by a different module.

1. Copy the function to the target module.
2. Update the original to delegate to the copy (or re-export it).
3. Run tests — green.
4. Migrate callers to the new location.
5. Remove the original.
6. Run tests — green.

Never move and modify in the same step.

### Extract Variable / Introduce Explaining Variable
**When:** a complex expression is hard to read; a magic number appears without
context.

```python
# Before
if user.created_at > datetime.now() - timedelta(days=30) and user.email_verified:

# After
is_recent_signup = user.created_at > datetime.now() - timedelta(days=30)
is_active = user.email_verified
if is_recent_signup and is_active:
```

### Replace Conditional with Polymorphism
**When:** a `switch`/`if-elif` checks a type or kind and branches on it; the
same pattern appears in multiple places.

1. Create an interface with the varying method.
2. Create one class per case, each implementing the interface.
3. Replace the conditional with a dispatch (registry or factory).
4. Tests stay green at each step.

See the `solid-design` skill (Open/Closed principle) for the full treatment.

### Extract Class / Split by Responsibility
**When:** a class has grown to handle two or more distinct responsibilities.

1. Identify the two responsibilities by asking "what are the two different
   reasons this class might change?"
2. Create the new class with the extracted responsibility.
3. Move fields and methods to the new class one at a time, running tests after
   each move.
4. Introduce the new class in the original class as a dependency (composition).
5. Update callers if the interface changes.

Never try to split a class in one step — the intermediate states will be broken.

### Inline Function / Inline Variable
**When:** a function or variable adds indirection without clarity — it is called
exactly once, its name is not clearer than its body, or it no longer earns the
abstraction.

Inline aggressively when the abstraction is not paying for itself. Not every
helper function is permanent.

### Replace Magic Number with Named Constant
```python
# Before
if retries > 3:

# After
MAX_RETRIES = 3
if retries > MAX_RETRIES:
```

### Introduce Parameter Object
**When:** the same group of parameters appears in multiple function signatures.

```python
# Before
def create_shipment(street, city, zip_code, country):
def validate_address(street, city, zip_code, country):

# After
@dataclass
class Address:
    street: str
    city: str
    zip_code: str
    country: str

def create_shipment(address: Address):
def validate_address(address: Address):
```

### Invert a Dependency (Apply DI)
**When:** business logic imports a concrete external dependency directly, making
it untestable without the external system.

1. Define the interface the business logic needs (owned by the consumer).
2. Extract the concrete implementation into an adapter class.
3. Change the business logic to receive the interface via its constructor.
4. Update the composition root to wire the concrete adapter in.
5. In tests, inject a fake/stub that implements the same interface.

See `solid-design` (Dependency Inversion) and `design-patterns` (Adapter) for
the full treatment.

---

## Refactoring procedure for a specific goal

### Preparing to add a feature

> "Make the change easy, then make the easy change."

1. Identify what the new feature needs: a new parameter, a new case in a
   conditional, a new type of object.
2. Refactor the existing code so the new case fits naturally — without adding
   the new case yet.
3. Commit the refactor (green suite).
4. Now add the new feature in the clean structure.
5. Commit the feature addition separately.

### Taming a god class

1. List all the responsibilities of the class.
2. Pick the responsibility with the least coupling to the others.
3. Extract it (Extract Class procedure above).
4. Repeat until each remaining class has one clear responsibility.
5. Each extraction is a separate, green commit.

### Removing duplication

1. Identify the duplicated logic (not just identical code — identical *intent*).
2. Write a test that exercises both copies through the same path (to confirm
   they have the same contract).
3. Extract the shared logic into a function or class.
4. Replace both copies with calls to the extraction.
5. Green suite after each step.

---

## What is NOT refactoring

- Changing observable behaviour (including fixing a bug) — this is a behaviour
  change, not a refactoring. Do it in a separate commit.
- Adding features — do this after the refactoring commit.
- Rewriting from scratch — this is replacement, not refactoring. Rewrites
  discard all the embedded knowledge about edge cases. Prefer incremental
  refactoring unless the code is genuinely unreachable.
- Changing tests to make a refactoring "pass" — tests that turn red during a
  refactoring mean the refactoring changed behaviour. Fix the code, not the test.

---

## Checklist before declaring a refactoring done

- [ ] Test suite is green at the end of every step, not just at the end.
- [ ] No behaviour changed — the only differences are structural.
- [ ] Each logical step is a separate, green commit.
- [ ] Characterisation tests were written first (if tests were missing).
- [ ] No new features, bug fixes, or "while I'm here" changes mixed in.
- [ ] The code is now easier to change for the next person than it was before.
