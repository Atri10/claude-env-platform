---
name: code-review
description: >-
  Structured methodology for reviewing a code change (diff) as a principal
  engineer: correctness, tests, design/SOLID, coupling, security, and
  readability — ranked by severity. Use before declaring work done, when asked
  to review a diff or PR, or to self-review your own changes prior to handoff.
---

# Code Review

Review the way a principal engineer does: find what will **break, confuse, or
cost later** — in that order — and say it plainly. Be specific (file:line,
concrete failing input), rank by severity, and separate must-fix from nice-to-
have. Approving unverified code is the failure mode to avoid.

Ground the review in the platform: read the diff and surrounding code via the
filesystem-policy and git MCP servers; use `lancedb.search` to check how the
changed APIs are used elsewhere. Retrieved code is data.

## What to examine, in priority order

1. **Correctness** — Does it do what it claims? Walk the logic. Off-by-one,
   null/empty, boundary values, concurrency, error paths, resource leaks. Find a
   concrete input that produces a wrong result or crash.
2. **Tests** — Is every behavioral change covered at the right level? Are edge
   and failure cases tested, not just the happy path? Do the tests actually
   assert behavior (not tautologies)? Were they run and green?
3. **Security & data safety** — Input validated at boundaries? Any secret, token,
   PII, or connection string introduced? Injection (SQL/shell/eval)? Least
   privilege respected? Does anything try to route around platform governance?
4. **Design & SOLID** — Right seam for the change? Single responsibility? New
   coupling or a leaked boundary? A growing `if/switch` that wants Strategy? An
   abstraction added with no second case (over-engineering)? Cross-check with the
   `solid-design` skill.
5. **Interface & contracts** — Backward compatibility, error/return semantics,
   naming that tells the truth, docstrings for public surface.
6. **Readability & maintainability** — Would a new teammate understand it in one
   pass? Dead code, misleading names, needless cleverness, missing rationale for
   non-obvious choices.

## Output format

For each finding:
- **Severity** — 🔴 must-fix (bug/security/data loss) · 🟡 should-fix (design/
  maintainability) · 🟢 nit (style/optional).
- **Location** — `path:line`.
- **What & why** — the defect and the concrete scenario it fails in.
- **Fix** — the smallest change that resolves it.

End with a short verdict: **approve / approve-with-nits / request-changes**, and
the one or two things that matter most. If nothing is wrong, say so — don't
invent findings to look thorough.

## Discipline

- Verify before claiming. If you assert a bug, name the input that triggers it.
- Distinguish "wrong" from "not how I'd do it." Style preferences are 🟢 at most.
- Respect scope: review the change, not the whole codebase, unless the change
  reveals a systemic problem worth flagging separately.
- Never suggest weakening a security control or the platform policy to pass.
