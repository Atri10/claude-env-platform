"""Coverage for the terminal MCP server's no-shell command chaining
(mcp-servers/terminal/server.py). Run: pytest tests/ -q

Regression context: the original _run() ran shlex.split(template) as a
literal argv, so anything an agent naturally writes with shell syntax --
'cd dir && npm test', 'grep foo | wc -l', 'pytest -q; echo done' -- failed
outright (';'/'&&'/'|'/'cd' aren't executables), which surfaced to the user
as "the command produced no output". A since-reverted fix attempted to solve
this by running commands via `bash -lc`, which is equivalent to shell=True
and defeats the module's core "no shell, no pipes, no interpolation"
guarantee (redirection, subshells, backgrounding, command substitution all
become live). This suite covers the real fix: tokenizing with shlex's
punctuation-char mode to recognize ';'/'&&'/'||'/'|' as their own tokens,
running each stage as its own argv via subprocess (never bash), handling
'cd' as a cwd change, and rejecting anything that would need a real shell
(>, >>, <, <<, &, (, )) with a clear error instead of silently doing
nothing or being handed to a shell interpreter.
"""
import importlib.util
import subprocess
import sys
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _load_server():
    """Import mcp-servers/terminal/server.py with its external deps
    (mcp, audit.audit_logger, lib.logging_setup) stubbed out, so this test
    exercises the real command-parsing/execution logic without needing a
    live MCP stdio session or the deployed $CLAUDE_ENV_HOME audit DB."""
    calls = []

    class _FakeAudit:
        def __init__(self, *a, **k):
            pass

        def tool_call(self, *a, **k):
            calls.append(("tool_call", a, k))

        def security_event(self, *a, **k):
            calls.append(("security_event", a, k))

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
    mod_log.get_logger = lambda *a, **k: logging.getLogger("test-terminal")
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

    spec = importlib.util.spec_from_file_location(
        "_terminal_server", _ROOT / "mcp-servers" / "terminal" / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._audit_calls = calls
    return mod


@pytest.fixture()
def server():
    return _load_server()


# --- tokenizer / operator recognition ---------------------------------------

def test_quoted_operator_stays_a_literal_word(server):
    toks = server._tokenize("echo 'a && b'")
    assert toks == ["echo", "a && b"]


def test_unquoted_operators_are_their_own_tokens(server):
    toks = server._tokenize("a && b || c; d | e")
    assert toks == ["a", "&&", "b", "||", "c", ";", "d", "|", "e"]


@pytest.mark.parametrize("op", [">", ">>", "<", "<<", "&", "(", ")"])
def test_rejected_shell_operators_raise(server, op):
    with pytest.raises(server._CommandError, match="unsupported shell operator"):
        server._tokenize(f"echo hi {op} out.txt")


def test_backtick_is_not_special_without_a_real_shell(server):
    # No shell is invoked, so a backtick is just a literal character in a
    # word -- not command substitution.
    toks = server._tokenize("echo `whoami`")
    assert toks == ["echo", "`whoami`"]


# --- pipeline splitting -------------------------------------------------------

def test_split_pipelines_groups_pipes_and_tracks_connectors(server):
    toks = server._tokenize("pytest -q && grep foo file | wc -l")
    pipelines = server._split_pipelines(toks)
    assert pipelines == [
        {"connector": None, "commands": [["pytest", "-q"]]},
        {"connector": "&&", "commands": [["grep", "foo", "file"], ["wc", "-l"]]},
    ]


@pytest.mark.parametrize("cmd", ["&& echo bad", "echo a |", "a ;; b"])
def test_stray_operator_raises_command_error(server, cmd):
    with pytest.raises(server._CommandError):
        server._split_pipelines(server._tokenize(cmd))


# --- end-to-end _run(): chaining works without a real shell -----------------

def test_simple_command_runs(server):
    out = server._run("echo hello", "run_tests")
    assert out == "exit=0\nhello\n"


def test_and_chain_runs_both_when_first_succeeds(server):
    out = server._run("echo one && echo two", "run_tests")
    assert "one" in out and "two" in out
    assert out.startswith("exit=0")


def test_and_chain_short_circuits_on_failure(server):
    out = server._run("false && echo should-not-print", "run_tests")
    assert "should-not-print" not in out
    assert "exit=1" in out


def test_or_chain_runs_fallback_on_failure(server):
    out = server._run("false || echo fallback", "run_tests")
    assert "fallback" in out
    assert out.startswith("exit=0")


def test_semicolon_chain_always_runs_second_stage(server):
    out = server._run("false ; echo still-ran", "run_tests")
    assert "still-ran" in out


def test_pipe_wires_stdout_to_stdin_without_a_shell(server):
    out = server._run("printf 'a\\nb\\na\\n' | grep a | wc -l", "run_tests")
    assert "exit=0" in out
    assert "2" in out


def test_cd_changes_cwd_for_rest_of_chain(server):
    out = server._run("cd rag && pwd", "run_tests")
    assert out.startswith("exit=0")
    assert str(_ROOT / "rag") in out


def test_cd_escaping_repo_root_is_rejected(server):
    out = server._run("cd ../../../.. && pwd", "run_tests")
    assert "escapes the allowed root" in out


def test_cd_nonexistent_dir_is_rejected(server):
    out = server._run("cd definitely-does-not-exist-xyz", "run_tests")
    assert "no such directory" in out


# --- rejected shell features surface a clear error, not silent failure ------

def test_redirection_is_rejected_not_silently_ignored(server):
    out = server._run("echo hi >> out.txt", "run_tests")
    assert out.startswith("ERROR:")
    assert "unsupported shell operator" in out
    assert not (_ROOT / "out.txt").exists()


def test_command_substitution_is_rejected(server):
    out = server._run("echo $(whoami)", "run_tests")
    assert out.startswith("ERROR:")


def test_empty_command_is_rejected(server):
    assert server._run("", "run_tests") == "ERROR: empty command"


def test_unknown_command_reports_not_found(server):
    out = server._run("cmd_that_does_not_exist_xyz", "run_tests")
    assert "command not found" in out


# --- no real shell is ever invoked -------------------------------------------

def test_run_never_spawns_a_shell_process(server, monkeypatch):
    """The whole point: assert no subprocess call ever names bash/sh as argv[0]."""
    seen_argvs = []
    real_popen = subprocess.Popen

    class _SpyPopen(real_popen):
        def __init__(self, argv, *a, **k):
            seen_argvs.append(list(argv))
            super().__init__(argv, *a, **k)

    monkeypatch.setattr(subprocess, "Popen", _SpyPopen)
    server._run("echo one && echo two | wc -l", "run_tests")
    for argv in seen_argvs:
        assert argv[0] not in ("bash", "sh", "/bin/bash", "/bin/sh"), argv
