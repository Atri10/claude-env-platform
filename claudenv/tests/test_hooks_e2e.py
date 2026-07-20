"""
End-to-end tests for the native-tool governance hooks.

Covers: path evaluation for Read/Write/Edit/Glob/Grep, Bash command-string
parsing (including redirections), network-egress gating, incident-mode
fail-closed, and that the HookInstaller wires every registered hook — including
the previously-unwired SessionHook.
"""
from __future__ import annotations

from claudenv.adapters.hooks.installer import HookInstaller
from claudenv.adapters.hooks.policy_hook import PolicyHook
from claudenv.adapters.hooks.session_hook import SessionHook
from claudenv.domain.policy import PolicyEngine


class FakeAudit:
    def tool_call(self, *a, **k):
        pass

    def agent_action(self, *a, **k):
        pass

    def security_event(self, *a, **k):
        pass

    def policy_violation(self, *a, **k):
        pass


def _engine(tier: int = 1, deny: list[str] | None = None) -> PolicyEngine:
    repo_data = {
        "version": 1, "tier": tier, "repo": "repo",
        "deny": {"paths": deny or []},
    }
    global_data = {
        "version": 1, "tier": 1, "deny": {"paths": []},
        "tiers": {tier: {"default_deny": False}},
    }
    return PolicyEngine.from_yaml(global_data, repo_data)


def _hook(tmp_path, tier=1, deny=None):
    return PolicyHook(_engine(tier=tier, deny=deny), FakeAudit(), repo_root=tmp_path)


def _call(hook, tool, **args):
    return hook.on_tool_call({"tool": tool, "args": args})


class TestPathEvaluation:
    def test_read_allowed(self, tmp_path):
        assert _call(_hook(tmp_path, deny=["secret.py"]), "Read", path="src/main.py") == {"allow": True}

    def test_read_blocked(self, tmp_path):
        res = _call(_hook(tmp_path, deny=["secret.py"]), "Read", path="secret.py")
        assert res["allow"] is False

    def test_write_blocked_path(self, tmp_path):
        res = _call(_hook(tmp_path, deny=["secret.py"]), "Write", path="secret.py", content="x")
        assert res["allow"] is False

    def test_edit_blocked_path(self, tmp_path):
        res = _call(_hook(tmp_path, deny=["secret.py"]), "Edit", path="secret.py",
                    new_string="x", old_string="y")
        assert res["allow"] is False

    def test_glob_and_grep_use_path_eval(self, tmp_path):
        hook = _hook(tmp_path, deny=["secret.py"])
        assert _call(hook, "Glob", path="secret.py")["allow"] is False
        assert _call(hook, "Grep", path="secret.py", pattern="x")["allow"] is False


class TestBashParsing:
    def test_bash_cat_blocked_path(self, tmp_path):
        res = _call(_hook(tmp_path, deny=["secret.py"]), "Bash", command="cat secret.py")
        assert res["allow"] is False

    def test_bash_redirect_target_blocked(self, tmp_path):
        res = _call(_hook(tmp_path, deny=["secret.py"]), "Bash", command="echo x > secret.py")
        assert res["allow"] is False

    def test_bash_stderr_redirect_blocked(self, tmp_path):
        res = _call(_hook(tmp_path, deny=["secret.py"]), "Bash", command="echo x 2> secret.py")
        assert res["allow"] is False

    def test_bash_safe_command_allowed(self, tmp_path):
        assert _call(_hook(tmp_path, deny=["secret.py"]), "Bash", command="echo hello") == {"allow": True}

    def test_bash_ls_safe_allowed(self, tmp_path):
        assert _call(_hook(tmp_path, deny=["secret.py"]), "Bash", command="ls -la src") == {"allow": True}

    def test_bash_network_egress_blocked_at_tier2(self, tmp_path):
        res = _call(_hook(tmp_path, tier=2, deny=[]), "Bash", command="curl http://example.com")
        assert res["allow"] is False
        assert "egress" in res["reason"].lower()

    def test_bash_network_egress_ask_at_tier1(self, tmp_path):
        res = _call(_hook(tmp_path, tier=1, deny=[]), "Bash", command="curl http://example.com")
        assert res["allow"] == "ask"


class TestIncidentFailClosedInHook:
    def test_hook_denies_everything_during_incident(self, tmp_path, monkeypatch):
        from claudenv.domain.incident import write_incident
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("CLAUDE_ENV_HOME", str(home))
        write_incident(reason="drill", by="operator", home=home)

        hook = _hook(tmp_path, deny=[])  # nothing blocked by policy
        assert _call(hook, "Read", path="src/main.py")["allow"] is False
        assert _call(hook, "Bash", command="echo hi")["allow"] is False


class TestInstallerWiring:
    def test_session_hook_is_wired(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        result = HookInstaller(repo).install()
        hooks = result["hooks"]
        assert "SessionStart" in hooks and "SessionEnd" in hooks
        session_start_cmds = [e["command"] for e in hooks["SessionStart"]]
        assert any("session_hook" in c for c in session_start_cmds)
        # Pre/Post hooks still wired exactly once.
        pre_cmds = [e["command"] for e in hooks["PreToolUse"]]
        post_cmds = [e["command"] for e in hooks["PostToolUse"]]
        assert sum("policy_hook" in c for c in pre_cmds) == 1
        assert sum("audit_hook" in c for c in post_cmds) == 1

    def test_register_pre_post_hook_drive_install(self, tmp_path):
        from claudenv.ports.hooks.interfaces import IPreToolUseHook

        class MyPre(IPreToolUseHook):
            HOOK_MATCHER = "Bash"
            HOOK_TIMEOUT = 9

            def on_tool_call(self, tool_call):
                return {"allow": True}

        repo = tmp_path / "repo"
        repo.mkdir()
        installer = HookInstaller(repo)
        installer._specs.clear()
        installer.register_pre_hook(MyPre)
        installer.register_post_hook(MyPre)
        result = installer.install()
        assert result["hooks"]["PreToolUse"][0]["command"].endswith(f"-m {MyPre.__module__}")
        assert result["hooks"]["PostToolUse"][0]["command"].endswith(f"-m {MyPre.__module__}")


class TestSessionHook:
    def _fake_audit(self, captured):
        class FakeAudit:
            def agent_action(self, agent, action, target=None, summary=None, success=True):
                captured.append((action, target))
        return FakeAudit()

    def test_records_session_start_and_end(self):
        captured = []
        SessionHook.run({"hook_event_name": "SessionStart", "session_id": "s1"},
                        audit=self._fake_audit(captured))
        SessionHook.run({"hook_event_name": "SessionEnd", "session_id": "s1"},
                        audit=self._fake_audit(captured))
        assert ("session_start", "s1") in captured
        assert ("session_end", "s1") in captured

    def test_unknown_event_records_nothing_and_never_raises(self):
        captured = []
        assert SessionHook.run({"hook_event_name": "Other"}, audit=self._fake_audit(captured)) == 0
        assert captured == []
        # Empty payload must not raise.
        assert SessionHook.run({}, audit=self._fake_audit(captured)) == 0
