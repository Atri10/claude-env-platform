# Repo-local governance hooks — design

Date: 2026-07-09
Status: approved (design), pending implementation

## Problem

The policy (`PreToolUse`) and audit (`PostToolUse`) hooks are installed into the
**global** `~/.claude/settings.json` by `hooks/install_hooks.py` (the default
target) and by `claude-env hooks`. Because that file is machine-wide, the hooks
fire in **every** Claude Code session on the machine — including repos that were
never onboarded and have no `.claude/repo-policy.yaml`. Those un-onboarded
sessions pay the hook cost and can hit confusing policy denies that were never
meant to apply to them.

## Goal

Governance hooks live in the **committed `<repo>/.claude/settings.json`** of
onboarded repos only. Un-onboarded repos are completely untouched. The global
hooks are removed. The hook command is written **portably** so the committed
`settings.json` works on any teammate's bootstrapped machine.

## Decisions (locked)

1. **Repo-local only.** Onboarding installs hooks into the repo's
   `.claude/settings.json`; the global `~/.claude/settings.json` hooks are not
   the default anymore. Existing repos are re-governed by re-onboarding (or
   running `claude-env hooks` inside the repo).
2. **Installed during onboard** (`register_repo.py`) as a first-class step, and
   also available standalone via `claude-env hooks` (which now defaults to the
   current repo).
3. **Target file: `.claude/settings.json`** — the team-shared, git-committed
   project settings, so governance travels with the repo when cloned. This file
   is already control-plane-protected (agents cannot edit it).
4. **Portable command via env-var expansion.** The hook command is written with
   the literal `$CLAUDE_ENV_HOME` env var (shell-expanded per machine), not the
   onboarding machine's absolute paths.

## Components & changes

### a) `hooks/install_hooks.py` — retarget + portable command
- Default `--settings` becomes `<cwd or --repo>/.claude/settings.json` instead of
  `~/.claude/settings.json`. Add an optional `--repo <path>` (defaults to cwd) so
  onboard can pass the target repo explicitly; `--settings` still overrides.
- The command strings change from resolved absolute paths to the literal
  env-var form so the committed file is portable:
  - `PRE_CMD  = '"$CLAUDE_ENV_HOME/venv/bin/python" "$CLAUDE_ENV_HOME/hooks/policy_hook.py"'`
  - `POST_CMD = '"$CLAUDE_ENV_HOME/venv/bin/python" "$CLAUDE_ENV_HOME/hooks/audit_hook.py"'`
  - Quoted so a path with spaces survives; Claude Code runs the hook command
    through a shell, which performs the `$CLAUDE_ENV_HOME` expansion.
- Add a `--global` flag that restores the old `~/.claude/settings.json` target
  (an escape hatch for machine-wide governance; not the default). `--uninstall`
  mirrors whatever target is in effect.
- Keep idempotency (`_has_cmd` dedupe by command string) and `--dry-run`.
- Uninstall must match the **new** portable command strings. To also clean up
  installs from the old absolute-path era, `_strip_cmd` matches by both the new
  literal command AND any command whose text ends with `hooks/policy_hook.py` /
  `hooks/audit_hook.py` (so a legacy absolute-path entry is removed too).

### b) `scripts/register_repo.py` — install hooks as an onboard step
- After the repo-policy step and alongside the `.claude/` template copy, invoke
  the installer against `<repo>/.claude/settings.json` (import and call, or
  subprocess to `install_hooks.py --repo <repo>`). Print a summary line like the
  other steps (`hooks -> .claude/settings.json`).
- Respect `--no-template` (skip the hook install too, since it is part of the
  in-repo `.claude/` deliverable). Add nothing to `~/.claude.json`.
- If global hooks are detected in `~/.claude/settings.json`, print a one-line
  notice that they can be removed with `claude-env hooks --uninstall --global`.
  **Do not** auto-delete them (surprising, machine-wide side effect).

### c) `claude-env hooks` (via `bin/claude-env`)
- `bin/claude-env` already passes args through to `install_hooks.py`, so no
  dispatch change is needed. Behavior follows from (a): `claude-env hooks`
  targets the current repo; `claude-env hooks --global` targets the machine;
  `--uninstall` mirrors the target.

### d) The hooks themselves — mostly unchanged
- `policy_hook.py` / `audit_hook.py` already resolve `CLAUDE_ENV_HOME`, add it to
  `sys.path`, and derive `repo` from `cwd`. No logic change is required for
  repo-locality.
- **Portability hardening:** confirm the import-time `sys.path` block cannot
  raise before the `main()` try/except guard, so that on a machine where
  `CLAUDE_ENV_HOME` is unset/missing the hook fails cleanly (log + allow, or deny
  under `CLAUDE_ENV_HOOK_FAIL_CLOSED`) instead of crashing the editor. The
  existing failure posture (fail-open unless `FAIL_CLOSED`) is preserved.

### e) "Get all data" — audit fidelity
- The hooks already capture the full dataset (tool, path, decision, secret hits,
  session id, `repo`). Moving to repo-local loses nothing and **sharpens** the
  ledger: every fired event now unambiguously belongs to an onboarded repo.
- Small improvement, **only if the field/plumbing already exists** (verify first,
  no speculative schema changes): record the onboarded repo **slug** (from
  `.claude/repo-policy.yaml`) consistently rather than only the directory
  basename, so audit rows key-match the RAG/memory namespaces. If this requires a
  schema or `AuditLogger` signature change, it is out of scope for this spec.

### f) Settings.json merge safety
- Onboard must never clobber a repo's existing `.claude/settings.json`. The
  installer's JSON `setdefault` merge + command-string dedupe already preserves
  unrelated keys and hooks. Covered by a test (below).

## Testing (`tests/`)
- Default install target is repo-local `.claude/settings.json` (not `~`).
- The emitted command strings use the literal `$CLAUDE_ENV_HOME` env var, not
  absolute paths.
- `--global` restores the `~/.claude/settings.json` target.
- Re-install is idempotent (no duplicate hook entries).
- A pre-existing unrelated hook / settings key survives the merge.
- `--uninstall` removes both the new portable command and a legacy absolute-path
  command.
- Onboard (`register_repo.py`) wires the hooks into `<repo>/.claude/settings.json`;
  `--no-template` skips them.
- An un-onboarded repo has no hooks (regression guard for the whole point).

## Docs to update (kept in sync per the claude-env-development skill)
- `docs/guide/native-tool-hooks.md` — wiring, "Configuration reference", and
  "How to use it" sections (currently say `~/.claude/settings.json`); document
  the portable env-var command, the repo-local default, and `--global`.
- `docs/guide/onboarding.md` — add the new hook-install step to the flow.
- `README.md` — the `claude-env hooks` examples (~lines 500–504) and the Hooks
  component blurb (which reference `~/.claude/settings.json`).
- `docs/guide/policy-engine.md` — the self-protection note that references
  `settings.json` scope, if the repo-local move changes what it says.

## Out of scope
- Any `AuditLogger`/DB schema change (the slug improvement is conditional on the
  plumbing already existing).
- Changing the `PRE_MATCHER` / `POST_MATCHER` tool sets.
- Global self-guard hook or `--global` fallback-by-default (explicitly rejected:
  repo-local only).

## Security tradeoff (accepted, by the "repo-local only" decision)
Under the old machine-wide install, the native-tool hooks enforced the
`global-policy.yaml` self-protection deny list (`**/.claude-env/**`,
`**/.claude/settings.json`, `**/.ssh/**`, …) in **every** session on the machine.
With hooks scoped per-repo, a session in a **never-onboarded** repo runs no policy
hook, so *native-tool* access to those paths there is no longer intercepted. This
is the deliberate consequence of the chosen design (it is exactly the "un-onboarded
repos are untouched" goal), and it is narrow:
- Onboarded repos are fully governed (unchanged) — and their committed
  `.claude/settings.json` is still deny-listed + control-plane-protected, so an
  agent cannot edit away its own governance.
- The `filesystem-policy` MCP server still governs any MCP-routed access in any
  repo regardless of onboarding, so the exposure is limited to Claude Code's
  *native* tools in a repo that was never onboarded.
The `--global` escape hatch remains for anyone who wants the old machine-wide
posture back.

## Deploy note
Per the platform workflow, edited hooks/installer/scripts must be re-mirrored to
`$CLAUDE_ENV_HOME` (`python3 bootstrap.py --no-deps` or targeted `cp`), and Claude
Code restarted, for the changes to take effect.
