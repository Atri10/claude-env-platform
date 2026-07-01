# repo-onboarding template

This directory is the **payload installed into a target repository** when it is
onboarded to the claude-env platform:

```
claude-env register /abs/path/to/repo            # patch MCP env + install this template
python scripts/register_repo.py /abs/path/to/repo # equivalent
```

## What gets installed

| Source (here)                              | Destination (in the repo) | Purpose |
|--------------------------------------------|---------------------------|---------|
| `CLAUDE.md`                                | `<repo>/CLAUDE.md`        | MCP-first governance operating contract (managed block). |
| `.claude/skills/*/SKILL.md`                | `<repo>/.claude/skills/`  | principled-engineering, solid-design, design-patterns, code-review, architecture-review. |
| `.claude/agents/*.md`                      | `<repo>/.claude/agents/`  | `code-reviewer`, `architecture-reviewer` subagents (read-only). |

## Placeholders

`CLAUDE.md` uses `{{REPO_NAME}}` and `{{TIER}}`, substituted at install time by
`scripts/register_repo.py`. The tier is read from the repo's
`.claude/repo-policy.yaml` (`tier:` field), defaulting to `1`.

## Install semantics (idempotent)

- **CLAUDE.md** — the text between `<!-- CLAUDE-ENV:BEGIN (managed) -->` and
  `<!-- CLAUDE-ENV:END -->` is owned by the platform and refreshed on every
  register. Text outside the markers (your project-specific guidance) is
  preserved. If a repo already has a CLAUDE.md with no managed block, the block
  is prepended and the existing content kept.
- **`.claude/` skills & agents** — copied only if absent. Pass `--force-template`
  to overwrite them with the current versions. Use `--no-template` to skip the
  template step entirely and only patch MCP env vars.

Edit the files here (in the platform), not the copies in a repo — repo copies of
the managed CLAUDE.md block are regenerated on the next register.
