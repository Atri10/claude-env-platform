"""Coverage for the PreToolUse hook's network-tool gating, out-of-repo read
confinement, and deny-message provenance. Run: pytest tests/ -q

Regression for three gaps a real onboarded-repo session exposed (2026-07-09):
  * Gap 1 — the hook matcher covered Bash but NOT the native WebFetch/WebSearch
    tools, so a model whose `curl` was denied could reach the network by simply
    switching to WebFetch. The hook was never even invoked. Now WebFetch/
    WebSearch egress is gated exactly like Bash egress (deny tier>=2, ask <=2).
  * Gap 2 — reads were policy-checked only against in-repo allow rules and global
    deny globs; a path resolving OUTSIDE the onboarded repo root (a sibling repo,
    ~/.claude/projects/*, ~/.claude/settings.json) matched no deny glob and was
    allowed. Cross-repo snooping walked right through. Out-of-repo reads are now
    hard-denied, except an explicit operator allow-list (scratchpad, tmp,
    $CLAUDE_ENV_HOME/state).
  * Gap 3 — deny reasons carried no provenance, so the model classified the whole
    governance layer as prompt injection and tried to route around it. Every
    deny/ask reason now carries a stable operator signature.

These drive hooks/policy_hook.main() end-to-end over stdin (the WebFetch and
read-scope logic lives in main(), not a helper), plus the helper units.
"""
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import hooks.policy_hook as ph
from hooks.policy_hook import SIGNATURE


def _repo(tier: int = 1) -> str:
    d = tempfile.mkdtemp()
    os.makedirs(d + "/.claude")
    os.makedirs(d + "/src")
    Path(d, "src", "app.py").write_text("print('hi')\n")
    policy = (_ROOT / "config" / "repo-policy.template.yaml").read_text()
    if tier != 1:
        policy = policy.replace("tier: 1", f"tier: {tier}", 1)
    Path(d, ".claude", "repo-policy.yaml").write_text(policy)
    return d


def _run_main(payload: dict) -> dict | None:
    """Drive main() with a hook payload on stdin; return the parsed decision
    JSON (or None if the hook allowed silently)."""
    buf = io.StringIO()
    old_stdin = sys.stdin
    sys.stdin = io.StringIO(json.dumps(payload))
    try:
        with redirect_stdout(buf):
            rc = ph.main()
    finally:
        sys.stdin = old_stdin
    assert rc == 0, "the hook must always exit 0 (decision is in the JSON)"
    out = buf.getvalue().strip()
    return json.loads(out) if out else None


def _decision(payload: dict) -> str:
    d = _run_main(payload)
    if d is None:
        return "allow"
    return d["hookSpecificOutput"]["permissionDecision"]


# -- Gap 1: WebFetch/WebSearch egress ---------------------------------------

def test_webfetch_asked_on_low_tier():
    d = _repo(tier=1)
    payload = {"tool_name": "WebFetch", "cwd": d,
               "tool_input": {"url": "https://go.dev/dl/?mode=json"}}
    assert _decision(payload) == "ask"


def test_webfetch_denied_on_tier2():
    d = _repo(tier=2)
    payload = {"tool_name": "WebFetch", "cwd": d,
               "tool_input": {"url": "https://go.dev/dl/?mode=json"}}
    assert _decision(payload) == "deny"


def test_websearch_asked_on_low_tier():
    d = _repo(tier=1)
    payload = {"tool_name": "WebSearch", "cwd": d,
               "tool_input": {"query": "latest go version"}}
    assert _decision(payload) == "ask"


def test_websearch_denied_on_tier2():
    d = _repo(tier=2)
    payload = {"tool_name": "WebSearch", "cwd": d,
               "tool_input": {"query": "latest go version"}}
    assert _decision(payload) == "deny"


def test_net_tool_never_silently_allowed_when_policy_unavailable(monkeypatch):
    # egress is a hard invariant: if the tier can't be resolved, a net tool must
    # fall to 'ask' (or 'deny' under fail-closed) — never a silent allow.
    d = _repo(tier=1)

    def _boom(*a, **k):
        raise RuntimeError("policy engine unavailable")

    import security.policy_engine as pe
    monkeypatch.setattr(pe.PolicyEngine, "load", staticmethod(_boom))
    payload = {"tool_name": "WebFetch", "cwd": d, "tool_input": {"url": "https://x"}}
    assert _decision(payload) == "ask"


def test_webfetch_is_in_the_installed_matcher():
    # the gate is worthless if Claude Code never routes the tool to the hook.
    # Load PRE_MATCHER from the REPO file explicitly: importing policy_hook puts
    # $CLAUDE_ENV_HOME (the deployed mirror) first on sys.path, which would
    # otherwise shadow hooks.install_hooks with a possibly-stale deployed copy.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_repo_install_hooks", _ROOT / "hooks" / "install_hooks.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    tools = set(mod.PRE_MATCHER.split("|"))
    assert {"WebFetch", "WebSearch"}.issubset(tools), mod.PRE_MATCHER


# -- Gap 2: out-of-repo reads are hard-denied -------------------------------

def test_native_read_outside_repo_denied():
    d = _repo()
    # sibling repo / another user path — resolves outside the onboarded repo
    payload = {"tool_name": "Read", "cwd": d,
               "tool_input": {"file_path": str(Path.home() / ".claude" / "settings.json")}}
    assert _decision(payload) == "deny"


def test_native_read_other_project_transcripts_denied():
    d = _repo()
    payload = {"tool_name": "Read", "cwd": d,
               "tool_input": {"file_path": str(Path.home() / ".claude" / "projects" /
                                               "some-other-repo" / "sess.jsonl")}}
    assert _decision(payload) == "deny"


def test_bash_read_outside_repo_denied():
    d = _repo()
    payload = {"tool_name": "Bash", "cwd": d,
               "tool_input": {"command": f"cat {Path.home()}/.claude/settings.json"}}
    assert _decision(payload) == "deny"


def test_native_read_inside_repo_allowed():
    d = _repo()
    payload = {"tool_name": "Read", "cwd": d,
               "tool_input": {"file_path": str(Path(d) / "src" / "app.py")}}
    assert _decision(payload) == "allow"


def test_read_scratchpad_allowed():
    # the session scratchpad is an explicit operator allow — tools legitimately
    # write/read intermediate files there. Use the exact claude-<uid> pattern the
    # allow-root recognizes.
    d = _repo()
    uid = os.getuid() if hasattr(os, "getuid") else ""
    scratch = Path(tempfile.gettempdir()) / f"claude-{uid}" / "some-session" / "scratchpad"
    scratch.mkdir(parents=True, exist_ok=True)
    f = scratch / "notes.txt"
    f.write_text("x")
    payload = {"tool_name": "Read", "cwd": d, "tool_input": {"file_path": str(f)}}
    assert _decision(payload) == "allow"


def test_read_arbitrary_temp_outside_scratchpad_denied():
    # a blanket OS-temp allow-root would be an escape hatch; only the specific
    # claude-<uid> scratchpad subtree is exempt, not all of /tmp.
    d = _repo()
    other = Path(tempfile.gettempdir()) / "not-a-scratchpad" / "secret.txt"
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text("x")
    payload = {"tool_name": "Read", "cwd": d, "tool_input": {"file_path": str(other)}}
    assert _decision(payload) == "deny"


def test_out_of_repo_read_helper_confines_to_repo():
    # unit-level guarantee of the confinement layer, independent of ambient
    # global-policy state: a $HOME path outside the repo is out-of-scope; an
    # in-repo path and an OS-temp path are not. (The deployed-platform dir is
    # additionally covered by the global **/.claude-env/** deny glob — see
    # test_policy_engine.py — layered on top of this.)
    d = _repo()
    root = Path(d)
    assert ph._out_of_repo_read(str(Path.home() / ".ssh" / "id_rsa"), root) is True
    assert ph._out_of_repo_read(str(root / "src" / "app.py"), root) is False
    uid = os.getuid() if hasattr(os, "getuid") else ""
    scratch = Path(tempfile.gettempdir()) / f"claude-{uid}" / "x.txt"
    assert ph._out_of_repo_read(str(scratch), root) is False


def test_glob_outside_repo_denied():
    d = _repo()
    payload = {"tool_name": "Glob", "cwd": d,
               "tool_input": {"path": str(Path.home() / ".claude" / "projects")}}
    assert _decision(payload) == "deny"


# -- Gap 3: provenance on every deny/ask ------------------------------------

def test_deny_reason_is_signed():
    d = _repo()
    payload = {"tool_name": "Read", "cwd": d,
               "tool_input": {"file_path": str(Path.home() / ".claude" / "settings.json")}}
    out = _run_main(payload)
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert SIGNATURE in reason, reason


def test_ask_reason_is_signed():
    d = _repo(tier=1)
    payload = {"tool_name": "WebFetch", "cwd": d,
               "tool_input": {"url": "https://go.dev"}}
    out = _run_main(payload)
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert SIGNATURE in reason, reason


def test_signature_is_stable_and_identifies_operator_control():
    # the string a model reads must make clear this is an installed control,
    # not session/injected text, so it doesn't try to route around it
    low = SIGNATURE.lower()
    assert "claude-env" in low
    assert "operator" in low or "installed" in low
