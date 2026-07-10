---
name: debugging
description: >-
  Systematic, hypothesis-driven debugging. Reproduce first, hypothesise,
  bisect, verify — never guess. Use when investigating a bug, an unexpected
  behaviour, a failing test, or an incident. Prevents the most common
  debugging failure modes: fixing symptoms, guessing without evidence, and
  introducing new bugs while chasing old ones.
---

# Systematic Debugging

Debugging is **scientific method applied to software**: observe, hypothesise,
design an experiment, measure, conclude. The failure mode is guessing — making
a change without understanding why it should work, which produces a codebase
full of mystery fixes that could be reverted silently.

This is a claude-env-governed repository. Read code and logs via the MCP
filesystem and git servers. Run diagnostic commands via `terminal.run_audit`
or `terminal.run_tests`. Do not run state-mutating commands without approval.

---

## The debugging procedure

### Step 1: Reproduce the bug — before touching any code

You cannot fix what you cannot reproduce. A fix applied to an unreproduced bug
is a guess, not a diagnosis.

- **Write the smallest reproducing case.** A failing test is ideal — it becomes
  the regression guard automatically. A minimal script that demonstrates the
  symptom is the next best thing.
- **Make it deterministic.** If the bug is intermittent, understand the
  condition that triggers it. "It fails sometimes" is not a reproduction — find
  the state or sequence that makes it fail reliably.
- **Confirm you have the right bug.** Run the reproduction and observe the
  failure. Read the error message fully — the most important information is
  often in the last line, not the first.
- **If you cannot reproduce it,** ask: is the bug environment-specific? Is it
  timing-dependent? What differs between the environment where it fails and
  where it doesn't?

### Step 2: Read the error in full — before searching for a fix

- Read the entire stack trace, not just the top frame. The root cause is usually
  several frames down from where the exception is thrown.
- Identify: what is the exact error message? what is the file and line number?
  what is the call chain that led there?
- Look for the word "caused by" or "from" — these signal the underlying error,
  not the propagated one.
- **Do not search for the error message online or apply a suggested fix before
  understanding it.** Copy-paste fixes to misunderstood errors are the source
  of many new bugs.

### Step 3: Form a hypothesis — one at a time

A hypothesis is a testable claim about the cause:
> "I believe X is wrong because Y, and if I'm right, then Z should be
> observable."

- A good hypothesis is **specific and falsifiable**: "the timestamp comparison
  fails because `created_at` is stored in UTC but compared in local time" is
  a hypothesis. "Something is wrong with dates" is not.
- Form **one hypothesis at a time**. Testing multiple hypotheses simultaneously
  makes it impossible to know which fix worked.
- Before testing, write down the hypothesis. If you skip this, you will not
  notice when the evidence contradicts it.

### Step 4: Design a minimal experiment

For each hypothesis, design the smallest possible experiment:
- A test assertion, a `print`/`log` statement, a breakpoint, a data query.
- The experiment must be capable of **disproving** the hypothesis. If the
  experiment can only confirm it, it is not a good experiment.
- Prefer adding observability (logging, assertions) over changing behaviour.
  Read before you write.

### Step 5: Run the experiment and interpret the result

- Does the observation match the prediction? → hypothesis supported (not
  proven — continue).
- Does the observation contradict the prediction? → hypothesis is wrong.
  Go back to step 3.
- Is the observation ambiguous? → refine the experiment.

**Never adjust your hypothesis to fit an unexpected result without explaining
why the result should be expected.** "Hmm, that's odd, but maybe…" is the
beginning of confusion.

### Step 6: Fix the root cause — not the symptom

- Fix the **root cause**, not the symptom. Suppressing the exception, adding a
  null check at the call site, or widening a type to avoid a cast are symptom
  fixes. They hide the real problem and accumulate as technical debt.
- Before applying any fix, state: "The root cause is X. My fix changes Y, which
  removes X." If you can't complete that sentence, you are fixing a symptom.
- Apply one change at a time. Don't bundle "while I'm here" improvements with
  the bug fix — separate commits, separate concerns.

### Step 7: Verify the fix

- Run the reproduction case — it should now pass.
- Run the full test suite — nothing that was passing should now be failing.
- If the bug had no test: write the regression test now (red without the fix,
  green with it). Commit it alongside the fix.

---

## Bisection — finding where a regression was introduced

When you know "it worked before" but not exactly when it broke:

1. **`git bisect start`** — start the binary search.
2. **`git bisect bad`** — mark the current (broken) commit.
3. **`git bisect good <known-good-commit>`** — mark the last known good commit.
4. Git checks out the midpoint. Run the reproduction case.
5. **`git bisect bad`** if still failing, **`git bisect good`** if passing.
6. Repeat until `git bisect` identifies the exact commit that introduced the bug.
7. **`git bisect reset`** — return to HEAD.
8. Read that commit's diff — that is where the root cause lives.

This is always faster than reading code trying to guess where it broke.

---

## Common failure modes to avoid

| Failure mode | What it looks like | Remedy |
|-------------|-------------------|--------|
| Fixing without reproducing | "I changed X and it seems fine now" | Reproduce first, always |
| Testing multiple hypotheses at once | Changed 4 things, now it works, don't know which | One change per experiment |
| Fixing the symptom | Added a null check where the value should never be null | Ask why the null exists |
| Stopping at the wrong frame | Fixed the exception site, not the cause | Read the full stack trace |
| Confirming bias | Only running tests that the fix passes | Design experiments that can falsify |
| Adding complexity to avoid understanding | Catching and suppressing, retrying blindly | Understand before fixing |
| Not writing a regression test | "It's fixed, I'll remember" | Always write the test |

---

## Debugging in this codebase

- **Read logs** via the audit ledger (`claude-env approvals --list-open`,
  session replay) for governance-related issues.
- **Read policy decisions** via `filesystem-policy` MCP server responses —
  if a path is unexpectedly denied, `evaluate_path` will tell you why.
- **Inspect git history** via `git.log` and `git.diff` MCP tools — for
  regressions, `git bisect` is the fastest path to the cause.
- **Use `lancedb.search`** to find all call sites of a function before
  concluding a change is safe — the bug may be in how the function is called,
  not in the function itself.
- **Do not modify audit events or governance files** while debugging — route
  all reads through the MCP servers; any write requires `terminal.run` approval.

---

## When you are stuck

In order:
1. **Take a break and re-read the error message.** The answer is often there.
2. **Explain the problem to a rubber duck** (or write it out). The act of
   explaining forces precision and often surfaces the answer.
3. **Bisect.** If the bug is a regression, `git bisect` always finds it.
4. **Add more observability.** Add logging at the seams, not in the middle of
   logic. Log inputs and outputs at each boundary.
5. **Simplify the reproduction case.** Remove everything that is not necessary
   to trigger the bug. The smallest reproduction is the clearest diagnosis.
6. **Read the source of the library or framework** that is behaving unexpectedly.
   The documentation is sometimes wrong; the source is not.
