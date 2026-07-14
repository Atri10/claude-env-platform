# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `claude-env init`: idempotent one-time setup that provisions `$CLAUDE_ENV_HOME`
  (`state/`, `config/`, `knowledge/`, `logs/`), copies the packaged default config,
  applies the SQL schema, and writes the genesis audit event.
- Packaged default data (`config/`, SQL schema, onboarding templates) now ships inside the
  wheel under `claudenv/_data/`, resolved via `importlib.resources` — the package is
  self-contained and installable with `pip install claudenv`.
- Open-source project scaffolding: `LICENSE`, `CONTRIBUTING.md`, `SECURITY.md`,
  `CODE_OF_CONDUCT.md`, this changelog, GitHub issue/PR templates, and a CI workflow.

### Changed
- MCP servers launch as installed modules (`python -m claudenv.adapters.mcp.<srv>.server`)
  instead of filesystem paths under `$CLAUDE_ENV_HOME`.
- Config resolution is deployed-first, packaged-fallback: the user-editable copy under
  `$CLAUDE_ENV_HOME/config/` takes precedence over the packaged default.
- Documentation (`CLAUDE.md`, `pyproject.toml`) updated for the hexagonal `claudenv/`
  package layout.

### Removed
- The standalone `bootstrap.py` and its mirror-to-`$CLAUDE_ENV_HOME` deploy model, replaced
  by standard packaging plus `claude-env init`.

## [1.0.0]

- Initial platform: policy-enforced filesystem access, tamper-evident hash-chained audit
  ledger, local RAG (llama.cpp + ONNX + LanceDB), local SQLite memory graph, approvals and
  incident mode, and the repo onboarding flow.

[Unreleased]: https://github.com/Atri10/claude-env-platform/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/Atri10/claude-env-platform/releases/tag/v1.0.0
