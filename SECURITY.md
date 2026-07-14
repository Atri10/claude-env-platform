# Security Policy

claude-env is a security tool: a local governance layer that enforces file-access policy,
maintains a tamper-evident audit ledger, and keeps AI-assisted development on your own
machine. A vulnerability here can undermine those guarantees, so we take reports seriously.

## Supported versions

The `master` branch and the latest released version receive security fixes. Older versions
are not maintained.

## Reporting a vulnerability

**Please do not open a public issue for security vulnerabilities.**

Instead, report privately via GitHub's
[private vulnerability reporting](https://github.com/Atri10/claude-env-platform/security/advisories/new)
("Report a vulnerability" under the Security tab). Include:

- A description of the issue and its impact.
- Steps to reproduce (a minimal proof of concept is ideal).
- Affected version/commit and platform.
- Any suggested remediation.

We aim to acknowledge reports within a few days and to work with you on a coordinated
disclosure timeline. Please give us a reasonable window to release a fix before any public
disclosure.

## What is in scope

The core security properties this project defends — a break in any of these is a valid
report:

- **Policy bypass** — reaching the filesystem (read or write) in a way the policy engine
  should have denied, including via the native-tool hooks or crafted Bash command strings.
- **Audit tampering** — mutating or deleting `audit_events`, or otherwise making
  `verify_chain()` pass over a forged history.
- **Unintended network egress** — the platform is local-only; any outbound traffic beyond
  the tier-gated documentation fetch is in scope.
- **Prompt injection / RAG or memory poisoning** — retrieved or external text being
  treated as instructions rather than data.
- **Secret leakage** — secrets/PII bypassing the content scanner into logs, RAG, or memory.
- **Approval-gate bypass** — running a state-mutating or gated command without the required
  human approval.

## Out of scope

- Vulnerabilities in third-party dependencies (report those upstream; we will bump once a
  fix ships).
- Issues requiring an already-compromised host or physical access.
- The bundled model weights / inference backends themselves (llama.cpp, ONNX Runtime).

## Handling secrets

If you find credentials, API keys, or tokens committed to this repository, please report
them privately as above rather than opening a public issue, so they can be rotated.
