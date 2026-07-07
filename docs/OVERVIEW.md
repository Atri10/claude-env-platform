# claude-env — Product Overview

*Seven problems that show up when you give an AI coding agent real access to a repo — and what claude-env does about each one.*

This document is for anyone evaluating or onboarding to claude-env. Each section
below starts with a problem you'd actually recognize, names the feature(s) that solve
it, and walks through a concrete example. For exhaustive installation steps, CLI
flags, and config schemas, see [`README.md`](../README.md) — this document links out
to it rather than repeating it.

---

## Table of contents

1. [The agent could read or touch something it shouldn't](#1-the-agent-could-read-or-touch-something-it-shouldnt)
2. [No one knows what the agent actually did](#2-no-one-knows-what-the-agent-actually-did)
3. [Risky actions run without anyone checking](#3-risky-actions-run-without-anyone-checking)
4. [The agent forgets everything, every session](#4-the-agent-forgets-everything-every-session)
5. [Knowledge and search shouldn't leave the building](#5-knowledge-and-search-shouldnt-leave-the-building)
6. [One generalist agent doing everything, badly](#6-one-generalist-agent-doing-everything-badly)
7. [You can't tell if it's working well or costing too much](#7-you-cant-tell-if-its-working-well-or-costing-too-much)
8. [How this actually gets turned on](#8-how-this-actually-gets-turned-on)

---

## Architecture at a glance

Before the details: claude-env sits between Claude Code and your machine. Every path
an agent could use to touch a file, run a command, retrieve knowledge, or remember
something is routed through one of two enforcement surfaces — **MCP servers** or
**native-tool hooks** — both of which consult a single **policy engine** and write to
a single **audit ledger**. Nothing bypasses this chokepoint, and nothing leaves your
machine except an optional, tier-gated documentation fetch.

![claude-env overall architecture](assets/architecture.svg)

---

## 1. The agent could read or touch something it shouldn't

**The problem.** You ask an agent to "fix a bug in the billing module." It technically
also has read access to `secrets/prod.env`, three folders over, because it's in the
same repo. Nothing about the instruction stops it from opening that file if it decides
that's relevant — and unlike a human teammate, it won't necessarily know to hesitate.

**What solves it:** the **policy engine**, extended to every native tool by **hooks**,
backed by **secret & prompt-injection detection**, with an **incident** kill switch for
when something is actively going wrong, and **policy simulation** so changes can be
tested before they go live.

**How it works.** Every file access — whether through claude-env's own tools or
Claude Code's native Read/Write/Edit/Bash — is checked against a layered policy: a
global deny list nothing can override, then a tier system (0 = public ... 3 =
restricted/default-deny), then repo-specific rules. Deny always wins. Even if an agent
tries to read a blocked file via a raw shell command instead of a file-read tool
(`cat secrets/prod.env`), the same check catches it, because the hook parses the
command string itself. Content inside allowed files is also scanned for secrets and
injection attempts, in case something sensitive ended up somewhere it technically
shouldn't be blocked. And if something looks actively wrong mid-session, a single
incident switch denies everything, everywhere, until it's turned off.

**Example.**
> A repo has `src/`, `docs/`, `tests/`, and `secrets/prod.env`. You ask the agent to
> "refactor the payment retry logic." It reads and edits files in `src/` and `tests/`
> freely. It also tries `cat secrets/prod.env` to "check the current API key format" —
> the request is blocked and logged before the file is ever opened, and the agent gets
> back a policy-denied response instead of the file contents. You never had to tell it
> not to look there; the policy already didn't allow it.

*Implemented in [`security/policy_engine.py`](../security/policy_engine.py),
[`hooks/policy_hook.py`](../hooks/policy_hook.py),
[`hooks/audit_hook.py`](../hooks/audit_hook.py),
[`security/detectors.py`](../security/detectors.py),
[`security/incident.py`](../security/incident.py),
[`security/policy_sim.py`](../security/policy_sim.py).*

---

## 2. No one knows what the agent actually did

**The problem.** Something breaks. Was it the agent? What exactly did it change, in
what order, and can you trust the log — or could it have been edited after the fact
(by a bug, or by whatever caused the problem in the first place)?

**What solves it:** a **tamper-evident audit ledger**, plus **session replay** and
**compliance reporting** built on top of it.

**How it works.** Every action an agent takes — file access, command run, retrieval,
memory write — is written to a **hash chain**: each new entry's hash is built from the
previous entry's hash. Change or delete anything in the past, and the chain breaks in
a way that's mathematically detectable — not just "we didn't notice," but provably
tamper-evident. The database itself refuses update/delete operations on this table, so
it's not just a convention anyone could bypass. On top of that ledger, session replay
reconstructs a full timeline of any past session, and compliance reports package up
ledger evidence with cryptographic proof of integrity, not just a log dump.

**Example.**
> A production config got overwritten last Tuesday and nobody's sure if it was the
> agent. You run `verify_chain()` — it comes back clean, proving no entry has been
> altered — then pull up session replay for that day. It shows, in order, exactly
> which files the agent touched and when, and the config change isn't among them. You
> just ruled out the agent with proof, not a guess.

*Implemented in [`audit/audit_logger.py`](../audit/audit_logger.py),
[`audit/session_replay.py`](../audit/session_replay.py),
[`audit/compliance_report.py`](../audit/compliance_report.py).*

---

## 3. Risky actions run without anyone checking

**The problem.** Some actions are legitimate but consequential enough that they
shouldn't happen unsupervised — running a command that touches external state, or
anything outside an agent's normal scope. You want a human to say yes first, not find
out after.

**What solves it:** the **approvals workflow**, and a **terminal** server that only
runs allow-listed commands in the first place.

**How it works.** The terminal MCP server won't execute arbitrary shell commands — only
ones on an allow-list, with no shell interpretation (so no `&&`, pipes, or injection
tricks), a scrubbed environment, and a timeout. If an agent wants to run something
outside that allow-list, it doesn't get silently blocked or silently allowed — it opens
an approval request, launches a local web UI, and **blocks** until a human clicks
approve or deny. The ledger records exactly who approved it (the real OS user and
host), not just a generic "approved" flag.

**Example.**
> An agent decides it needs to run a one-off script to regenerate test fixtures — not
> on the allow-list. Instead of failing or running anyway, it opens an approval
> request. You get a prompt, read what it wants to run, and click approve. The agent
> resumes; the ledger now shows exactly what ran, that it required approval, and that
> you were the one who approved it.

![claude-env request lifecycle](assets/request-lifecycle.svg)

*Implemented in [`mcp-servers/terminal/server.py`](../mcp-servers/terminal/server.py),
[`agents/orchestration/approvals_ui.py`](../agents/orchestration/approvals_ui.py),
[`agents/orchestration/approval_gate.py`](../agents/orchestration/approval_gate.py).*

---

## 4. The agent forgets everything, every session

**The problem.** You explain an architectural decision to the agent on Monday. By
Thursday, in a new session, it's forgotten — you're re-explaining the same context
over and over, and nothing persists across sessions or gets shared with teammates.

**What solves it:** a local **memory graph**, automatic **session ingestion**,
**consolidation & pruning** to keep it useful over time, and **team knowledge sync** to
share it deliberately.

**How it works.** Decisions, entities, and architectural facts are stored as a graph
of nodes and edges in local SQLite — scoped to that repo's own namespace, so one
project's memory never bleeds into another's. Older memories lose confidence over time
unless reinforced, the way a human's recollection fades, so stale information doesn't
quietly dominate forever. A nightly job reads Claude Code session transcripts and
turns them into memory automatically (redacting secrets first), and another nightly
job consolidates clusters of low-confidence memories into cleaner summaries and
archives (never silently deletes) what's pruned. When a team wants to share what one
person's agent has learned, memory can be exported and imported by namespace, secrets
redacted, without handing over someone's entire memory store.

**Example.**
> Three months ago the team decided to standardize on a specific date-handling
> library. That decision is still in memory today with high confidence because it's
> been referenced since. A one-off debugging tangent from a single session six weeks
> ago has faded and no longer surfaces — but it's archived, not gone, if you ever need
> it back. A new teammate's claude-env setup imports the project's memory namespace and
> starts with that context already in place.

*Implemented in [`memory/memory_manager.py`](../memory/memory_manager.py),
[`memory/memory_retriever.py`](../memory/memory_retriever.py),
[`memory/session_ingestor.py`](../memory/session_ingestor.py),
[`memory/memory_consolidator.py`](../memory/memory_consolidator.py),
[`memory/memory_pruner.py`](../memory/memory_pruner.py),
[`memory/memory_sync.py`](../memory/memory_sync.py).*

---

## 5. Knowledge and search shouldn't leave the building

**The problem.** For an agent to be useful on a large codebase, it needs to search and
retrieve relevant context efficiently — but sending your code to a third-party
embedding API to make that search work means your code leaves the machine. For a lot
of repos, that's simply not acceptable.

**What solves it:** a fully local **RAG (retrieval-augmented generation) pipeline**,
poison-screened **retrieval**, and **MCP servers** that keep every one of these
capabilities behind a local, stdio-only interface — no ports, no external calls.

**How it works.** Each repo gets its own local knowledge index: code is chunked
AST-aware (a chunk is a real function or class, not an arbitrary slice of text),
embedded with a local model (llama.cpp), optionally reranked with a local ONNX
cross-encoder, and searched via LanceDB — vector and full-text combined. No model name
is hardcoded anywhere; it's chosen in config, so a team can swap embedding models
without touching code. Retrieved content is always treated as *data*, never as
*instructions* — it's screened for injection or "poisoning" attempts before an agent
ever sees it, so a manipulated document in your own docs folder can't hijack agent
behavior. The only server with any network capability at all is the documentation
server, and even that only fetches externally for lower-sensitivity (tier 0-1) repos.

**Example.**
> You ask the agent "where do we validate incoming webhook signatures?" It searches
> the local index, finds the right function in `src/webhooks/verify.py`, and returns
> it — no code, embeddings, or query ever left your machine. Separately, someone
> committed a doc file containing text designed to look like an instruction ("ignore
> previous guidance and...") — when that file gets retrieved, the poison screener flags
> it before the agent treats it as anything other than reference text.

*Implemented in [`rag/retrievers/lance_store.py`](../rag/retrievers/lance_store.py),
[`rag/config.py`](../rag/config.py),
[`rag/pipelines/retrieve.py`](../rag/pipelines/retrieve.py), and the six servers under
[`mcp-servers/`](../mcp-servers/) (filesystem-policy, git, lancedb-rag, memory-graph,
terminal, documentation).*

---

## 6. One generalist agent doing everything, badly

**The problem.** A single agent with full permissions, asked to do everything from
writing docs to touching production infrastructure, is both a poor fit for
specialized work and a large, unnecessary attack surface — the documentation task and
the database migration have very different risk profiles, but a generalist agent
treats them the same.

**What solves it:** **specialist agents**, each with a narrow, explicit scope, plus
**task routing/handoff** and **conflict resolution** to coordinate them.

**How it works.** Instead of one agent, claude-env defines specialists (architect,
backend, database, devops, documentation, frontend, performance, research, security,
testing) plus an orchestrator, each with its own registry entry defining exactly which
tools it can use, which paths it can write to, and whether its actions require human
approval. An orchestrator decomposes a task and routes each piece to the right
specialist, handing off only the scoped context that specialist actually needs — not
the whole session history. When specialists disagree, a conflict resolver reconciles
the outcome, with one deliberate rule: the security specialist's objection wins,
regardless of how the other votes fall.

**Example.**
> You ask for "a new API endpoint, with tests and docs." The orchestrator splits this
> into three handoffs: backend writes the endpoint, testing writes the tests,
> documentation updates the docs — each only sees the slice of context relevant to its
> part. Separately, the performance specialist proposes caching some data in a way the
> security specialist flags as unsafe; the conflict resolver sides with security,
> regardless of which suggestion seemed more efficient.

*Implemented in [`agents/agent_registry.yaml`](../agents/agent_registry.yaml),
[`agents/orchestration/task_router.py`](../agents/orchestration/task_router.py),
[`agents/orchestration/agent_handoff.py`](../agents/orchestration/agent_handoff.py),
[`agents/orchestration/conflict_resolver.py`](../agents/orchestration/conflict_resolver.py).*

---

## 7. You can't tell if it's working well or costing too much

**The problem.** Local inference isn't free — compute adds up, and if any documentation
fetches go external there's real cost. Meanwhile, retrieval quality and overall system
health tend to only be visible when something has already gone wrong.

**What solves it:** **budgets & cost tracking**, a **feedback loop** that improves
retrieval over time, a local **dashboard**, a **validation suite**, and **nightly
automation** that keeps maintenance from depending on someone remembering to run it.

**How it works.** Token usage and estimated cost are tracked per repo against a
configurable price table, with monthly caps enforced so usage can't silently run past
budget. A feedback loop correlates which retrieved chunks actually led to real edits
and boosts their ranking over time, so search quality improves the more the system is
used. A local, read-only dashboard makes policy/audit/memory/RAG state browsable
without querying the database by hand. A validation suite checks installation health,
security configuration, memory integrity, agent registry correctness, and MCP server
startup — so "is everything actually working" has a fast, concrete answer. And nightly
jobs (systemd/launchd) handle memory consolidation, pruning, incremental re-indexing,
and digest generation automatically.

**Example.**
> A client repo is capped at a monthly budget. Usage is visible well before it's hit,
> not discovered afterward. After a platform upgrade, you run the validation suite
> instead of hoping nothing broke — it checks the audit chain, policy syntax, memory
> integrity, and MCP server startup in one pass, and tells you exactly what (if
> anything) needs attention before you trust it with real work again.

*Implemented in [`observability/budgets.py`](../observability/budgets.py),
[`config/budgets.yaml`](../config/budgets.yaml),
[`observability/feedback.py`](../observability/feedback.py),
[`observability/dashboard.py`](../observability/dashboard.py),
[`validation/`](../validation/),
[`scripts/nightly_memory.sh`](../scripts/nightly_memory.sh),
[`agents/analysts/nightly_analyst.py`](../agents/analysts/nightly_analyst.py).*

---

## 8. How this actually gets turned on

Bringing a repository under claude-env's governance is one command:
`claude-env onboard` (see [`scripts/register_repo.py`](../scripts/register_repo.py)).
It generates a starting policy for that repo, creates an isolated RAG index and memory
namespace, and installs a separate `CLAUDE.md` + `.claude/` directory (skills and two
read-only reviewer subagents) *into* the onboarded repo — a different deliverable from
claude-env's own root `CLAUDE.md`, which governs the platform itself. Templates for
what gets installed live in
[`templates/repo-onboarding/`](../templates/repo-onboarding/).

---

## What's next

- For exhaustive installation steps, CLI command reference, and full YAML config
  schemas, see [`README.md`](../README.md).
- Deeper, module-by-module technical documentation will live under `docs/guide/` as it
  is written.
