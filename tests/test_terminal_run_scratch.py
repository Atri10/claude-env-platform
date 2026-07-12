"""Coverage for terminal.run_scratch (mcp-servers/terminal/server.py) --
unattended command execution in the per-repo scratch directory, protected by
two enforcement layers instead of the human-approval gate terminal.run uses.
Run: pytest tests/ -q

Layer 1 (pre-execution): security.command_inspector.inspect_command() checks
the command's file-looking arguments against the SAME policy engine that
protects filesystem.read/write, so `cat /path/to/repo/.env` or
`cp /path/to/repo/secrets/x .` is refused before anything executes.
Layer 2 (post-execution): PolicyEngine.scan_content() -- the SAME method
filesystem.read/write already call -- redacts any secret-shaped stdout/stderr
(e.g. an AWS-key-shaped string) before it reaches the agent.

Regression context: terminal.run's ONLY defense today is the human-approval
block. Removing that block for scratch commands without also porting
policy_hook.py's path-deny/egress logic would reopen exactly the
exfiltration path the platform's invariants exist to close (see
docs/superpowers/specs/2026-07-12-secure-scratchpad-design.md).
"""
import importlib.util
import subprocess
import sys
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _load_server(repo_root: Path, home: Path):
    calls = []

    class _FakeAudit:
        def __init__(self, *a, **k):
            pass

        def tool_call(self, *a, **k):
            calls.append(("tool_call", a, k))

        def security_event(self, *a, **k):
            calls.append(("security_event", a, k))

        def policy_violation(self, *a, **k):
            calls.append(("policy_violation", a, k))

        def human_approval_request(self, *a, **k):
            return "req-1"

    mod_audit_pkg = types.ModuleType("audit")
    mod_audit = types.ModuleType("audit.audit_logger")
    mod_audit.AuditLogger = _FakeAudit
    sys.modules["audit"] = mod_audit_pkg
    sys.modules["audit.audit_logger"] = mod_audit

    mod_lib_pkg = types.ModuleType("lib")
    mod_log = types.ModuleType("lib.logging_setup")
    import logging
    mod_log.get_logger = lambda *a, **k: logging.getLogger("test-terminal-scratch")
    sys.modules["lib"] = mod_lib_pkg
    sys.modules["lib.logging_setup"] = mod_log

    mod_mcp = types.ModuleType("mcp")
    mod_mcp_server = types.ModuleType("mcp.server")
    mod_mcp_server.Server = lambda name: types.SimpleNamespace(
        list_tools=lambda: (lambda f: f), call_tool=lambda: (lambda f: f))
    mod_mcp_stdio = types.ModuleType("mcp.server.stdio")
    mod_mcp_stdio.stdio_server = None
    mod_mcp_types = types.ModuleType("mcp.types")
    mod_mcp_types.TextContent = None
    mod_mcp_types.Tool = None
    sys.modules["mcp"] = mod_mcp
    sys.modules["mcp.server"] = mod_mcp_server
    sys.modules["mcp.server.stdio"] = mod_mcp_stdio
    sys.modules["mcp.types"] = mod_mcp_types

    import os
    os.environ["CLAUDE_ENV_REPO_ROOT"] = str(repo_root)
    os.environ["CLAUDE_ENV_HOME"] = str(home)

    spec = importlib.util.spec_from_file_location(
        "_terminal_scratch_server", _ROOT / "mcp-servers" / "terminal" / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._audit_calls = calls
    return mod


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "demo-repo"
    (repo / ".claude").mkdir(parents=True)
    (repo / ".claude" / "repo-policy.yaml").write_text(
        (_ROOT / "config" / "repo-policy.template.yaml").read_text())
    (repo / ".env").write_text("SECRET_KEY=super-secret-value\n")
    return repo


@pytest.fixture()
def repo_and_server(tmp_path):
    """Note: SCRATCH_ROOT ($CLAUDE_ENV_HOME/scratch/<repo>/) and the repo root
    are UNRELATED directory trees -- scratch is not nested inside the repo.
    So a relative '../secret' from inside scratch does NOT resolve to the
    real repo's secret; tests that need to prove 'a scratch command can't
    reach the real repo's protected files' must use an ABSOLUTE path to the
    repo, which is the realistic vector (an agent knows REPO_ROOT and could
    construct an absolute path to it)."""
    repo = _repo(tmp_path)
    home = tmp_path / "home"
    (home / "scratch" / "demo-repo").mkdir(parents=True)
    srv = _load_server(repo, home)
    return repo, srv


def test_scratch_root_matches_filesystem_policy_convention(repo_and_server, tmp_path):
    _repo_path, srv = repo_and_server
    expected = tmp_path / "home" / "scratch" / "demo-repo"
    assert srv.SCRATCH_ROOT == expected.resolve()


def test_run_scratch_allows_ordinary_command(repo_and_server):
    _repo_path, srv = repo_and_server
    out = srv._run_scratch("echo hello")
    assert out.startswith("exit=0")
    assert "hello" in out


def test_run_scratch_allows_its_own_deny_shaped_file(repo_and_server):
    """Per the design's decision 3, scratch is allow-all for ITS OWN files --
    an agent-created '.env'-named scratch file is not blocked purely by
    extension match, unlike the real repo's .env (see the next test)."""
    _repo_path, srv = repo_and_server
    srv._run_scratch("echo not-a-real-secret | tee local.env")
    out = srv._run_scratch("cat local.env")
    assert out.startswith("exit=0")
    assert "not-a-real-secret" in out


def test_run_scratch_blocks_read_of_denied_path_outside_scratch(repo_and_server):
    repo_path, srv = repo_and_server
    # absolute path to the real repo's .env -- the realistic escape vector,
    # since scratch and the repo are unrelated directory trees (a relative
    # '../.env' from scratch does not resolve to this file at all).
    out = srv._run_scratch(f"cat {repo_path / '.env'}")
    assert out.startswith("BLOCKED:")
    assert "protected path" in out


def test_run_scratch_blocks_copy_of_denied_path_into_scratch(repo_and_server):
    repo_path, srv = repo_and_server
    out = srv._run_scratch(f"cp {repo_path / '.env'} ./copy.env")
    assert out.startswith("BLOCKED:")


def test_run_scratch_redacts_secret_shaped_output(repo_and_server):
    # AKIA[0-9A-Z]{16} matches unquoted -- unlike the generic_api_key pattern,
    # which requires the value itself to be quoted (api_key="...").
    _repo_path, srv = repo_and_server
    out = srv._run_scratch("echo AKIAABCDEFGHIJ12345K")
    assert "AKIAABCDEFGHIJ12345K" not in out
    assert "REDACTED" in out


def test_run_scratch_does_not_require_approval(repo_and_server):
    """The whole point: no human_approval_request call, no blocking wait."""
    _repo_path, srv = repo_and_server
    srv._run_scratch("echo hi")
    assert not any(call[0] == "human_approval_request" for call in srv._audit_calls)


def test_run_scratch_never_spawns_a_shell(repo_and_server, monkeypatch):
    _repo_path, srv = repo_and_server
    seen_argvs = []
    real_popen = subprocess.Popen

    class _SpyPopen(real_popen):
        def __init__(self, argv, *a, **k):
            seen_argvs.append(list(argv))
            super().__init__(argv, *a, **k)

    monkeypatch.setattr(subprocess, "Popen", _SpyPopen)
    srv._run_scratch("echo one && echo two")
    for argv in seen_argvs:
        assert argv[0] not in ("bash", "sh", "/bin/bash", "/bin/sh"), argv


def test_run_scratch_allows_destructive_command_on_its_own_files(repo_and_server):
    """Unlike the native-tool hook, destructive commands are not hard-denied
    in scratch -- only their path arguments are still policy-checked. Uses
    'tee' rather than '>' redirection, which the no-shell engine rejects
    outright (see _PUNCTUATION_CHARS in mcp-servers/terminal/server.py)."""
    _repo_path, srv = repo_and_server
    srv._run_scratch("echo data | tee myfile.txt")
    out = srv._run_scratch("rm myfile.txt")
    assert out.startswith("exit=0")


def test_run_scratch_egress_denied_at_tier_2(tmp_path):
    repo = tmp_path / "tier2-repo"
    (repo / ".claude").mkdir(parents=True)
    policy = (_ROOT / "config" / "repo-policy.template.yaml").read_text()
    policy = policy.replace("tier: 1", "tier: 2", 1)
    (repo / ".claude" / "repo-policy.yaml").write_text(policy)
    home = tmp_path / "home"
    (home / "scratch" / "tier2-repo").mkdir(parents=True)
    srv = _load_server(repo, home)

    out = srv._run_scratch("curl https://example.com")
    assert out.startswith("BLOCKED:")
    assert "network egress" in out


# ---------------------------------------------------------------------------
# Fix pass: Finding 1 (Critical) -- structural path confinement.
#
# inspect_command()'s named-pattern deny check was designed for
# filesystem.read/write, which can only ever touch paths already inside the
# repo. terminal.run_scratch spawns a real OS process whose arguments can
# name ANY path the machine's user can read -- e.g. `cat ~/.aws/credentials`
# matches no configured deny pattern at all (no rule names '.aws' or a bare
# 'credentials' file), so the named-pattern check alone would let it sail
# through and leak the file's contents straight back to the agent.
# confine_to_roots() closes this by denying any command whose file arguments
# resolve outside BOTH REPO_ROOT and SCRATCH_ROOT, regardless of the path's
# name.
# ---------------------------------------------------------------------------

def test_run_scratch_denies_path_outside_both_roots_even_without_named_pattern(
        repo_and_server, tmp_path):
    """The Critical gap this fix closes: a machine-wide credential file that
    is NOT shaped like any configured deny pattern (not named .env/secrets/
    .ssh/etc.) but resolves outside both REPO_ROOT and SCRATCH_ROOT must still
    be denied -- structurally, not by name. Uses a simulated credential file
    under tmp_path (never the real ~/.aws) so the test can't touch the actual
    machine."""
    _repo_path, srv = repo_and_server
    fake_home = tmp_path / "fake_home" / ".aws"
    fake_home.mkdir(parents=True)
    cred_file = fake_home / "credentials"
    cred_file.write_text(
        "[default]\naws_access_key_id = FAKEIDNOTREAL\n"
        "aws_secret_access_key = totally-not-a-real-secret-value\n")

    # Sanity check: the named-pattern layer alone would allow this path --
    # proves the new check is doing independent work, not just re-detecting
    # something Layer 1 already caught.
    from security.command_inspector import inspect_command
    named_pattern_verdict = inspect_command(
        f"cat {cred_file}", str(srv.SCRATCH_ROOT), srv._policy_engine,
        root=srv.REPO_ROOT, exempt_root=srv.SCRATCH_ROOT)
    assert named_pattern_verdict.action == "allow"

    out = srv._run_scratch(f"cat {cred_file}")
    assert out.startswith("BLOCKED:")
    assert "outside the allowed directories" in out
    assert "totally-not-a-real-secret-value" not in out


def test_run_scratch_still_allows_scratch_local_and_repo_relative_work(
        repo_and_server):
    """Regression: ordinary scratch-local work and legitimate repo-relative
    work (absolute path to a non-denied file inside REPO_ROOT) must keep
    working exactly as before -- the new check is additive, not a
    replacement."""
    repo_path, srv = repo_and_server

    # scratch-local
    out = srv._run_scratch("echo scratch-local-ok | tee note.txt")
    assert out.startswith("exit=0")
    out = srv._run_scratch("cat note.txt")
    assert "scratch-local-ok" in out

    # repo-relative (absolute path into REPO_ROOT, non-denied file)
    (repo_path / "readme_ok.txt").write_text("fine to read\n")
    out = srv._run_scratch(f"cat {repo_path / 'readme_ok.txt'}")
    assert out.startswith("exit=0")
    assert "fine to read" in out


def test_run_scratch_named_pattern_deny_still_also_applies(repo_and_server):
    """Regression: the ORIGINAL named-pattern deny check (the real repo's
    .env, which resolves inside REPO_ROOT and so passes the new structural
    check) must still independently deny -- this fix is IN ADDITION to Layer
    1, not a replacement for it."""
    repo_path, srv = repo_and_server
    out = srv._run_scratch(f"cat {repo_path / '.env'}")
    assert out.startswith("BLOCKED:")
    assert "protected path" in out


# ---------------------------------------------------------------------------
# Fix pass: Finding 2 (Important) -- 'cd' is not supported in run_scratch.
#
# inspect_command() evaluates the WHOLE command string's file arguments
# against one static cwd (SCRATCH_ROOT), but the real executor (_run()) walks
# cwd across pipeline stages when an interior 'cd' succeeds -- a fragile
# inconsistency that could silently defeat a future path-anchored deny rule.
# The fix: run_scratch rejects any command containing a 'cd' token outright,
# before either enforcement layer runs.
# ---------------------------------------------------------------------------

def test_run_scratch_rejects_cd_in_a_chain(repo_and_server):
    _repo_path, srv = repo_and_server
    out = srv._run_scratch("mkdir -p sub && cd sub && cat ../../etc/passwd")
    assert out.startswith("BLOCKED:")
    assert "cd" in out.lower()
    assert "not supported" in out.lower()


def test_run_scratch_rejects_bare_cd(repo_and_server):
    _repo_path, srv = repo_and_server
    out = srv._run_scratch("cd ..")
    assert out.startswith("BLOCKED:")


def test_run_scratch_cd_rejected_before_execution(repo_and_server, tmp_path):
    """The rejection must happen BEFORE anything runs -- no side effect from
    the part of the chain preceding 'cd'."""
    _repo_path, srv = repo_and_server
    out = srv._run_scratch("mkdir -p sub && cd sub && cat file.txt")
    assert out.startswith("BLOCKED:")
    # the 'mkdir -p sub' stage must not have run either -- the whole command
    # is rejected atomically, not partially executed up to the 'cd'.
    assert not (srv.SCRATCH_ROOT / "sub").exists()
