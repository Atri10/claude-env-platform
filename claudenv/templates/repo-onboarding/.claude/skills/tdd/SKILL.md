---
name: tdd
description: >-
  Test-Driven Development — the red-green-refactor loop applied concretely.
  What to test, what not to test, the test pyramid, and how to write tests
  that survive refactors. Use when implementing any new behaviour, fixing a
  bug, or when asked to add tests to existing code.
---

# Test-Driven Development

TDD is not about tests as an afterthought — it is about **using tests to
clarify the design before writing implementation**. A test written first is a
specification: it defines what the code should do before you decide how. The
loop is: red (failing test defining the contract) → green (minimum code to
pass) → refactor (clean up without breaking anything).

This is a claude-env-governed repository: run tests via `terminal.run_tests`
(configured in `.claude/commands.json`), and report actual output — never
claim green on unverified code.

---

## The red-green-refactor loop

### Red — write a failing test that specifies the contract
- Write one test for the simplest interesting case.
- The test must fail *for the right reason*: not a compilation error, but a
  semantic assertion failure. If it fails to compile, fix the signature first.
- Keep the test small and focused: one logical assertion per test case.
- Run it and confirm it is red. If it is accidentally green, the test is wrong.

### Green — write the minimum code to make it pass
- Write **only** what is needed to make the test pass. No extra logic, no
  "while I'm here" additions.
- Duplication is acceptable at this stage — you will clean it in refactor.
- Run the full test suite, not just the new test. Nothing that was passing
  before should now be red.

### Refactor — clean up without changing behaviour
- Eliminate duplication, extract well-named functions, improve naming.
- After every change, run the full suite. If anything turns red, undo the
  last change — the refactor introduced a regression.
- The tests are your safety net here. If the refactor is safe, the tests stay
  green. If they turn red, the refactor was not safe.

Repeat for the next case. Add cases in order of complexity: happy path first,
then boundary values, then failure modes.

---

## What to test

### Test behaviour, not implementation
A test should assert the *observable contract* of a unit: given input X, the
output is Y, or the error is Z. It should not assert which private methods were
called, what the internal state is, or in what order internal operations ran.

**Why this matters:** a test that asserts implementation detail breaks every
time you refactor, even when the behaviour is unchanged. These tests fight
refactoring instead of enabling it.

```python
# Wrong: tests implementation detail
def test_user_save():
    mock_db.execute.assert_called_once_with("INSERT INTO users …")  # fragile

# Right: tests observable behaviour
def test_user_is_retrievable_after_save():
    repo.save(user)
    assert repo.find(user.id) == user  # contract
```

### What deserves a test
- **Every new behaviour.** If the code path didn't exist before, it needs a
  test before or alongside the implementation.
- **Every bug fix.** Write the test that exposes the bug first (it will be red),
  then fix the code (it turns green). The test prevents the regression.
- **Boundary values.** The edges of the domain are where bugs hide: empty
  collections, zero amounts, maximum lengths, null/None inputs, off-by-one
  indices.
- **Error paths.** Every error that the function is documented to raise or
  return should have a test asserting it fires under the right conditions.
- **Concurrency invariants.** If the code is concurrent: test that a shared
  resource is not corrupted under concurrent writes.

### What does not deserve a test
- **Third-party library internals.** Don't test that `requests.get` makes an
  HTTP call — that is the library's test to write.
- **Framework behaviour.** Don't test that Django routes correctly — test that
  your view function returns the right response.
- **Trivial getters/setters** with no logic.
- **Private implementation details** that will change as the design evolves.
- **Configuration values** — test the behaviour they enable, not the values.

---

## The test pyramid

```
         /\
        /  \   E2E / integration tests (few, slow, catch wiring bugs)
       /----\
      /      \  Integration tests (moderate — test module seams)
     /--------\
    /          \  Unit tests (many, fast, test logic in isolation)
   /____________\
```

**Unit tests** (the base) — fast, deterministic, no I/O. Test pure functions
and business logic. Stub all external dependencies. Should run in milliseconds.

**Integration tests** (the middle) — test that two or more real components wire
together correctly: a service + a real repository against a test DB, a handler
+ a real router. Slower, but catch wiring bugs that unit tests miss.

**E2E / acceptance tests** (the top) — test the full system from the outside.
Slow and expensive to maintain. Keep these few; reserve them for the critical
user journeys. If a unit test can cover it, use a unit test.

**Invert the pyramid at your peril.** A system with 500 E2E tests and 5 unit
tests is slow, brittle, and gives poor signal on where the bug is.

---

## Concrete procedure for a new feature

1. **Identify the unit under test.** What function, class, or module owns this
   behaviour? If you can't identify it, the design is unclear — clarify the
   boundary first.
2. **Write the test for the happy path first.** Name it
   `test_<unit>_<condition>_<expected_result>`. Example:
   `test_order_service_places_order_when_stock_available`.
3. **Run it — confirm red.** The assertion should fail, not a missing import.
4. **Write the minimum implementation.** Make it pass. No extras.
5. **Run the full suite — confirm green across the board.**
6. **Write the boundary case test.** Add the next most important case
   (empty input, zero value, max limit). Repeat red-green.
7. **Write the failure mode test.** What error does the function raise when
   given invalid input? Test it. Repeat red-green.
8. **Refactor.** Clean up duplication and naming. Suite stays green throughout.
9. **Run `terminal.run_tests` and report the actual output** before declaring done.

---

## Writing tests that survive refactors

- **Assert on the public contract**, not the internal implementation.
- **One logical assertion per test.** Multiple assertions in one test make it
  hard to know which one failed and why.
- **Name the test to describe the scenario**, not the method:
  `test_checkout_fails_when_cart_is_empty` not `test_checkout`.
- **Arrange-Act-Assert structure** makes tests readable:
  ```python
  def test_order_total_includes_tax():
      # Arrange
      order = Order(items=[Item(price=100)], tax_rate=0.1)
      # Act
      total = order.calculate_total()
      # Assert
      assert total == 110
  ```
- **Keep tests independent.** No shared mutable state between tests. Use
  setup/teardown (fixtures, `beforeEach`) to reset state. A test that passes
  alone but fails in a suite is a hidden dependency between tests — fix it.
- **Mock at the boundary, not in the middle.** Stub external services (network,
  DB, clock, random) at the outermost seam. Don't mock your own domain objects.

---

## Testing existing code without tests (characterisation)

When you need to refactor code that has no tests:

1. **Write characterisation tests first.** These capture the *current* behaviour
   (including any bugs) so you can refactor safely without accidentally changing
   it. They are the safety net, not the specification.
2. **Run and confirm green.** These tests should pass against the current code.
3. **Now refactor.** The characterisation tests will catch any unintended
   behaviour change.
4. **Separately write specification tests** for the correct behaviour. These may
   expose bugs — fix the bugs, update the characterisation tests to match.

See the `refactoring` skill for the full safe-refactoring procedure.
