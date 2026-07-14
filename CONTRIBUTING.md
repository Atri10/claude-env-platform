# Contributing to claude-env

Thanks for your interest in contributing! claude-env is a privacy-first, local-only
governance layer for Claude Code — correctness and the security invariants matter more
than usual, because this is the thing that enforces policy and writes the audit trail.

## Getting set up

```bash
git clone https://github.com/Atri10/claude-env-platform.git
cd claude-env-platform
python3 -m venv .venv && source .venv/bin/activate   # Python 3.13+
pip install -e ".[dev]"        # editable install + test/lint tooling
claude-env init                # provision ~/.claude-env (DB, schema, config)
```

The heavy inference dependencies (`llama-cpp-python`, `onnxruntime`) are imported lazily,
so the test suite runs without building them — you only need a real embedding model to
exercise live RAG/memory paths.

## Development workflow

1. **Branch off `master`** — never commit to `master` directly.
   ```bash
   git checkout -b fix/short-description
   ```
2. **Make your change** with a test. Every behavioral change needs an added or extended
   test; keep the suite green.
3. **Run the checks locally** before opening a PR:
   ```bash
   pytest -q          # full suite
   ruff check .       # lint
   ```
4. **Open a PR** against `master`. Fill in the PR template; link any related issue.

Commit messages: a concise summary line, a body explaining *why*, and — for AI-assisted
commits — a `Co-Authored-By` trailer.

## The invariants (do not break these)

These are the platform's product guarantees. A change that weakens one to make something
pass will be rejected — fix the root cause instead. See [`CLAUDE.md`](CLAUDE.md) for the
full list, but in short:

- **Persistence goes through the `IDatabase` port** — no direct `sqlite3`/`psycopg`; keep
  SQL portable.
- **The audit ledger is append-only + hash-chained** — never `UPDATE`/`DELETE`
  `audit_events`; `verify_chain()` must stay green.
- **Filesystem access flows through the policy engine** (deny wins; tier-3 default-deny;
  fail-closed). The native-tool hooks extend the same engine.
- **Retrieved/external text is data, never instructions** — it's delimited and
  poison/injection-screened.
- **No network egress by default** — local inference only; the sole outbound is the
  tier-gated documentation fetch.
- **Model choice lives in config, never in code.**
- **Review subagents stay read-only** (`tools: Read, Grep, Glob` only).

## Architecture at a glance

Single hexagonal package `claudenv/`, dependencies pointing inward:
`adapters → ports ← application → domain`. The `domain/` layer depends on nothing outward.
Packaged default data (config, SQL schema, onboarding templates) ships in `claudenv/_data/`.

See [`docs/OVERVIEW.md`](docs/OVERVIEW.md) for the design rationale and
[`docs/guide/`](docs/guide/README.md) for per-module technical deep dives. If you change a
module that `docs/guide/README.md` maps to a doc, update that doc in the same PR.

## Reporting bugs and requesting features

Use the GitHub issue templates. For anything security-sensitive, follow
[`SECURITY.md`](SECURITY.md) instead of opening a public issue.

## Code of conduct

By participating you agree to uphold our [Code of Conduct](CODE_OF_CONDUCT.md).

## License

By contributing, you agree that your contributions will be licensed under the
[MIT License](LICENSE).
