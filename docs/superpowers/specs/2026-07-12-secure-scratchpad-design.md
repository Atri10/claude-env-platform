# Secure scratch pad for unattended command execution — design

Date: 2026-07-12
Status: approved (design), pending implementation

## Problem

Agents naturally want a place to do intermediate work — download/build artifacts,
write throwaway scripts, run a training loop, chain commands with `&&`/`|` — without
every single step opening a human-approval request (the current `terminal.run`
behavior). But the platform's invariants must hold even when nobody is watching each
command in real time:

- no read of a denied path (`.env`, `secrets/**`, `.ssh/**`, `*.pem`, etc.) may
  succeed, even indirectly via `cat`/`grep`/`cp` inside a scratch command
- no secret-shaped output may reach the agent's context unredacted
- no network egress beyond what tier policy already allows
- the scratch area itself must not become a way to leak repo secrets into a
  location the model can freely read back

Today `hooks/policy_hook.py` already solves exactly this problem for Claude Code's
**native** Bash tool (tokenize the command, extract file-looking arguments, deny
network-egress combos, ask on inline secrets) — but that logic doesn't exist inside
`terminal` MCP's `_run()`, and `terminal.run`'s only defense today is the human
approval gate. Remove that gate for scratch commands without also porting this
logic, and you reopen exactly the exfiltration path the platform exists to close.

## Decisions (locked)

1. **Scope: per-repo, not per-session.** `CLAUDE_ENV_SESSION` is not actually wired
   to a shared value across MCP server processes today (every server independently
   falls back to its own hardcoded default when unset — confirmed via grep, no code
   path sets it). Introducing real cross-process session identity is out of scope
   for this feature. `CLAUDE_ENV_REPO_ROOT` (and `CLAUDE_ENV_REPO_NAME`) IS already
   resolved identically and reliably by every MCP server (`scripts/register_repo.py`
   fills these into each server's env block at onboard time). Scratch is keyed off
   the repo, not an undefined session: one `scratch/<repo_name>/` directory per
   repo, wiped at the start of each Claude Code session (not shared across
   concurrent sessions on the same repo — acceptable, confirmed with the user).

2. **Execution model: unattended.** New tool `terminal.run_scratch` — no human
   approval, no blocking wait. This is the entire point (fast iteration). Safety is
   achieved mechanically (decisions 4-5 below), not by a human checkpoint.

3. **Policy scope inside scratch: repo policy applies, allow-shaped.** Scratch
   itself is an allow-all zone (an agent can create/read/write anything *it wrote*
   there), but the SAME deny rules that protect the real repo (global + repo deny
   paths/extensions/regex, tier default-deny) still apply to anything a scratch
   command reads or writes **outside** the scratch directory — see decision 4.
   Scratch never grants access to paths that would otherwise be denied.

4. **Two-layer enforcement inside `terminal.run_scratch`, ported from
   `hooks/policy_hook.py` (not reinvented):**
   - **Layer 1 — pre-execution path deny-check.** Tokenize the command, extract
     file-looking arguments (the exact technique `_bash_candidates`/`_shell_tokens`
     already use), resolve each candidate path, and run it through
     `PolicyEngine.evaluate_path()`. If any candidate resolves to a denied path,
     **refuse the whole command — nothing executes.**
   - **Layer 2 — post-execution output scan.** Run stdout/stderr through
     `security.detectors.SecretDetector` (the same `SECRET_PATTERNS` used
     elsewhere) and redact matches before the result reaches the agent, mirroring
     what `filesystem.read`/`filesystem.write` already do via
     `PolicyEngine.scan_content()`.
   - **Network egress detection**, reusing the same `_bash_candidates` pass (it
     already extracts network commands): deny on tier≥2 (network disabled), ask/log
     on tier≤1 — matching `_inspect_bash`'s existing precedence.
   - This logic is **extracted from `hooks/policy_hook.py` into a shared module**
     (`security/command_inspector.py`) so the native-tool hook and terminal MCP
     both call one implementation instead of maintaining two parsers that could
     drift apart.

5. **Binaries never go through `filesystem.write`'s text payload.** MCP tool
   content is a JSON/text channel; base64-encoding multi-GB model checkpoints or
   datasets through it is both wasteful and unnecessary. Binaries are produced by
   scratch commands writing directly to disk inside the scratch directory
   (`terminal.run_scratch` with a command like `python train.py -o model.bin`);
   `filesystem.read/write/list` (extended with the `scratch://` scheme) stay scoped
   to text/metadata files, with the existing `MAX_READ_BYTES`-style ceiling.

6. **Quota: fixed per-write cap + running counter, not a tree-walk.** Re-summing
   the whole scratch directory on every write doesn't scale. Track total bytes
   written in an in-memory counter (reset when the scratch dir is wiped), and cap
   individual writes going through `filesystem.write` at the same `MAX_READ_BYTES`
   order of magnitude. Commands run via `terminal.run_scratch` are not
   byte-counted per file (that would require intercepting every syscall); a
   coarser periodic `du`-style check is out of scope for v1 and left as a manual
   `claude-env scratch status` command instead (see decision 8).

7. **Cleanup: wipe-on-start + signal/atexit + TTL reaper backstop.**
   - `filesystem-policy` (as `startup_order: 1`, the first MCP server to start)
     wipes and recreates `$CLAUDE_ENV_HOME/scratch/<repo_name>/` at process start.
   - `atexit` + `SIGTERM`/`SIGINT` handlers attempt a clean removal on shutdown.
   - Neither covers `SIGKILL`/OOM/crash. Backstop: on every `bootstrap.py` run (and
     optionally a lightweight check in `filesystem-policy` startup), delete any
     `scratch/<x>` directory whose mtime is older than a TTL (default 24h,
     `CLAUDE_ENV_SCRATCH_TTL_HOURS`). Wipe-on-start (bullet 1) makes this mostly
     redundant in the common case, but protects the disk on machines that sit idle
     with a stale scratch dir between sessions that never cleanly restart the MCP
     servers.

8. **No free-form `cwd`.** `terminal.run_scratch` takes only `{"command": "..."}`;
   the scratch root is derived server-side from `CLAUDE_ENV_REPO_NAME`, never a
   path the agent supplies. (Superseded the earlier draft's `in_scratch` boolean
   on `terminal.run` — a dedicated tool name is clearer for audit/approval-bypass
   visibility than an easy-to-miss flag on the existing gated tool.)

## Components & changes

### a) `security/command_inspector.py` (NEW — extracted from `hooks/policy_hook.py`)

Move, verbatim in behavior, the following out of `hooks/policy_hook.py` into this
new shared module:
- `_shell_tokens`, `_bash_candidates`, `_bash_write_targets`, `_looks_like_path`
- `_FILE_CMDS`, `_NET_CMDS`, `_CMD_SEP`, `_REDIR` constants
- A new `inspect_command(command, cwd, engine) -> CommandVerdict` that runs the
  precedence chain (deny path > exfiltration > plain egress) — this is
  `_inspect_bash`'s logic minus the native-tool-specific bits (control-plane guard,
  mutating-native-shell guard, `_mcp_first_hint`), which stay in `policy_hook.py`
  since they only make sense for Claude Code's native Bash tool where destructive
  commands should route through `terminal.run`/Write/Edit instead. For
  `terminal.run_scratch`, destructive commands (`rm`, `dd`, etc.) are NOT hard
  denied by default (a scratch pad's entire point is disposable work — an agent
  should be able to `rm -rf` its own scratch files) but ARE still subject
  to the path-deny and egress checks (an `rm` targeting a denied path is still
  refused, since the deny check runs on any file-looking argument regardless of
  which command carries it).

`policy_hook.py` is updated to import `_shell_tokens`/`_bash_candidates`/etc. from
the new shared module instead of defining them locally — no behavior change for the
native-tool hook, verified by the existing `hooks/policy_hook.py` test suite passing
unmodified.

### b) `mcp-servers/filesystem-policy/server.py` — `scratch://` scheme

- `_resolve()` branches: paths starting with `scratch://` resolve under
  `$CLAUDE_ENV_HOME/scratch/<repo_name>/<rest>` (no session ID component — see
  decision 1); everything else resolves under `REPO_ROOT` as today.
- Escape checks (`relative_to(scratch_root)`) mirror the existing repo-root escape
  check exactly.
- `_enforce_path()` for scratch paths: allow-all for the scratch-local part of the
  path, but the write-target guard from `policy_hook.py`'s control-plane check does
  NOT apply here (scratch dirs are never control-plane paths by construction) —
  simplifies to: any read/write **inside** scratch succeeds unconditionally (still
  subject to `scan_content()` secret redaction on read/write, same as today), and
  the resolver itself is what prevents escaping the scratch directory.
- On process start (before `stdio_server()` begins), wipe and recreate the repo's
  scratch directory. Register `atexit`/signal cleanup.

### c) `mcp-servers/terminal/server.py` — `terminal.run_scratch` tool

- New tool, listed in `list_tools()`, with a description that plainly states: no
  approval, no blocking, sandboxed to the scratch directory, subject to path-deny
  and egress screening (Layer 1) and output secret-redaction (Layer 2).
- Implementation reuses the existing `_tokenize`/`_split_pipelines`/`_run_pipeline`
  chaining machinery built in the prior `fix/terminal-safe-command-chaining` work —
  no new shell-parsing logic, just a new cwd (the scratch dir) and the two new
  enforcement layers wrapped around the existing `_run()`.
- Before executing (Layer 1): call `command_inspector.inspect_command(command,
  cwd=scratch_dir, engine=<PolicyEngine loaded for this repo>)`. On `deny`, return
  `BLOCKED: <reason>` and log a `policy_violation` — nothing runs.
- After executing (Layer 2): run `SecretDetector().redact()` over stdout/stderr
  before returning; if secrets were found, log a `security_event` (category
  `secret_redaction`, mirroring `filesystem.read`'s existing pattern) but still
  return the redacted output rather than blocking (matches the read-path
  precedent — redact, don't refuse, since the command already ran).
- `PolicyEngine` is loaded once at server start (same as `filesystem-policy`
  already does) so no per-call reload cost.

### d) `bootstrap.py` — TTL reaper backstop

- New function `reap_stale_scratch_dirs(ttl_hours=24)` called during
  `init_policies()` (or a new lightweight step): iterate
  `$CLAUDE_ENV_HOME/scratch/*`, delete any directory whose mtime exceeds the TTL.
  `CLAUDE_ENV_SCRATCH_TTL_HOURS` env override.

### e) `config/global-policy.yaml` — scratch section (documentation/config only)

```yaml
scratch:
  enabled: true
  ttl_hours: 24              # backstop reaper (bootstrap.py)
```

No new deny/allow rules needed here — scratch enforcement lives in code
(decision 3/4), this section only toggles the feature and the reaper TTL.

### f) `README.md` / `docs/guide/*` — document the new tool

Add `terminal.run_scratch` to the MCP tool reference table, explicitly noting it is
**unattended** (unlike `terminal.run`) and listing the two enforcement layers so
operators understand why it's safe to leave ungated.

## Data flow (worked examples)

**Blocked — command touches a denied path:**
```
agent -> terminal.run_scratch({"command": "cp ../.env ./copy.env"})
  Layer 1: _bash_candidates finds "../.env" and "./copy.env" as file-looking args
  -> resolve "../.env" relative to scratch cwd -> repo root .env
  -> PolicyEngine.evaluate_path(".env") -> BLOCK (global deny_ext ".env")
  -> refused before exec: "BLOCKED: command touches a protected path (.env)"
  -> policy_violation audit row written
```

**Allowed — secret-shaped output redacted, not blocked:**
```
agent -> terminal.run_scratch({"command": "env | grep API"})
  Layer 1: no file-looking args, no net cmd -> passes
  -> command executes, stdout: "API_KEY=sk-abc123..."
  Layer 2: SecretDetector matches generic_secret pattern
  -> output returned as "API_KEY=[REDACTED:generic_secret]"
  -> security_event(category=secret_redaction) logged
```

**Allowed — ordinary scratch work:**
```
agent -> terminal.run_scratch({"command": "python3 train.py -o model.bin && ls -la"})
  Layer 1: "model.bin" is a file-looking arg but not a denied path -> passes
  -> executes in $CLAUDE_ENV_HOME/scratch/<repo>/, writes model.bin to disk directly
  -> Layer 2: no secret patterns in stdout -> returned as-is
```

## Testing strategy

- `tests/test_command_inspector.py` (new): unit tests for the extracted
  `inspect_command`, covering deny-path detection, exfiltration combo,
  plain-egress tier behavior, and destructive-command passthrough (NOT hard-denied
  for scratch, unlike the native-tool hook).
- `tests/test_policy_hook*.py` (existing): must continue passing unmodified after
  the extraction — proves no native-tool behavior regressed.
- `tests/test_terminal_run_scratch.py` (new): end-to-end `_run` reuse, Layer 1
  block cases (denied path, exfiltration combo), Layer 2 redaction, and a "normal
  command with no findings" happy path — using the same MCP-stdio-stub pattern as
  `tests/test_terminal_command_chaining.py`.
- `tests/test_scratch_fs.py` (new): `scratch://` path resolution (happy path,
  escape attempt), wipe-on-start, `atexit`/signal cleanup registration.
- `tests/test_bootstrap_scratch_reap.py` (new): TTL reaper deletes old dirs,
  leaves recent ones.

## Explicitly out of scope for v1

- True per-Claude-Code-session isolation (would require wiring a real shared
  `CLAUDE_ENV_SESSION` across every MCP server's env block — a separate, larger
  change to `mcp-servers.json`/`register_repo.py` if ever needed).
- Per-file byte-level quota enforcement for commands run via
  `terminal.run_scratch` (only `filesystem.write` is capped in v1; a `du`-based
  `claude-env scratch status` command is a natural v2 follow-up, not built here).
- Any change to `terminal.run`'s existing approval-gated behavior — it is
  untouched; `terminal.run_scratch` is purely additive.
