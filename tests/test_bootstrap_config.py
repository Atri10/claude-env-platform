"""Coverage for bootstrap.py's deployed-config handling (init_policies /
_deploy_config). Run: pytest tests/ -q

Regression test for a real incident: bootstrap.py used to unconditionally
shutil.copy2 config/rag.yaml (and friends) into $CLAUDE_ENV_HOME on every
run, including routine `bootstrap.py --no-deps` re-syncs done after editing
platform code. Since config/rag.yaml's embedding.model_path is a blank
template in the repo (the real value is machine-local, edited only on the
deployed copy per README §4a), every re-bootstrap silently reverted a
working RAG setup back to an unconfigured one -- with no error until the
next retrieval attempt raised deep inside rag/config.py.
"""
import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("bootstrap_under_test",
                                               _ROOT / "bootstrap.py")
bootstrap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bootstrap)


def _fake_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo" / "config"
    repo.mkdir(parents=True)
    (repo / "global-policy.yaml").write_text("deny: []\n")
    (repo / "repo-policy.template.yaml").write_text("tier: 1\n")
    (repo / "rag.yaml").write_text("embedding:\n  model_path: \"\"\n")
    (repo / "mcp-servers.json").write_text("{}\n")
    (repo / "budgets.yaml").write_text("warn_at: 0.8\n")
    return repo.parent


def _home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    (h / "config").mkdir(parents=True)
    return h


def test_first_run_deploys_all_configs(tmp_path, monkeypatch):
    repo = _fake_repo(tmp_path)
    home = _home(tmp_path)
    monkeypatch.setattr(bootstrap, "REPO_DIR", repo)
    monkeypatch.setattr(bootstrap, "HOME", home)

    bootstrap.init_policies()

    for name in ("global-policy.yaml", "repo-policy.template.yaml", "rag.yaml",
                "mcp-servers.json", "budgets.yaml"):
        assert (home / "config" / name).exists(), f"{name} not deployed on first run"


def test_rerun_preserves_locally_edited_rag_yaml(tmp_path, monkeypatch):
    """The exact regression: a machine-local model_path must survive a
    routine re-bootstrap (e.g. `--no-deps` after editing platform code)."""
    repo = _fake_repo(tmp_path)
    home = _home(tmp_path)
    monkeypatch.setattr(bootstrap, "REPO_DIR", repo)
    monkeypatch.setattr(bootstrap, "HOME", home)

    bootstrap.init_policies()  # first run: deploys blank template
    dst_rag = home / "config" / "rag.yaml"
    dst_rag.write_text("embedding:\n  model_path: \"/models/real-model.gguf\"\n")

    bootstrap.init_policies()  # simulates a later `bootstrap.py --no-deps`

    assert "real-model.gguf" in dst_rag.read_text(), (
        "re-running init_policies() must not overwrite an already-deployed "
        "config file")


def test_rerun_preserves_budgets_and_mcp_servers_and_global_policy(tmp_path, monkeypatch):
    repo = _fake_repo(tmp_path)
    home = _home(tmp_path)
    monkeypatch.setattr(bootstrap, "REPO_DIR", repo)
    monkeypatch.setattr(bootstrap, "HOME", home)

    bootstrap.init_policies()
    for name, marker in (("budgets.yaml", "repos:\n  myrepo: 42\n"),
                        ("mcp-servers.json", '{"custom": true}\n'),
                        ("global-policy.yaml", "deny:\n  - custom/**\n")):
        (home / "config" / name).write_text(marker)

    bootstrap.init_policies()

    for name, marker in (("budgets.yaml", "myrepo: 42"),
                        ("mcp-servers.json", "custom"),
                        ("global-policy.yaml", "custom/**")):
        assert marker in (home / "config" / name).read_text(), \
            f"{name} local edit was overwritten on re-run"


def test_repo_policy_template_is_always_refreshed(tmp_path, monkeypatch):
    """Unlike the four hand-edited configs, the onboarding template is not a
    per-machine value store, so it should stay in sync with the repo."""
    repo = _fake_repo(tmp_path)
    home = _home(tmp_path)
    monkeypatch.setattr(bootstrap, "REPO_DIR", repo)
    monkeypatch.setattr(bootstrap, "HOME", home)

    bootstrap.init_policies()
    (repo / "config" / "repo-policy.template.yaml").write_text("tier: 2\n")

    bootstrap.init_policies()

    assert (home / "config" / "repo-policy.template.yaml").read_text() == "tier: 2\n"


def test_force_config_resets_to_repo_template(tmp_path, monkeypatch):
    repo = _fake_repo(tmp_path)
    home = _home(tmp_path)
    monkeypatch.setattr(bootstrap, "REPO_DIR", repo)
    monkeypatch.setattr(bootstrap, "HOME", home)

    bootstrap.init_policies()
    dst_rag = home / "config" / "rag.yaml"
    dst_rag.write_text("embedding:\n  model_path: \"/models/real-model.gguf\"\n")

    bootstrap.init_policies(force_config=True)

    assert "real-model.gguf" not in dst_rag.read_text(), \
        "--force-config must reset the deployed copy to the repo template"


def test_missing_source_warns_but_does_not_raise(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo" / "config"
    repo.mkdir(parents=True)   # no config files inside
    home = _home(tmp_path)
    monkeypatch.setattr(bootstrap, "REPO_DIR", repo.parent)
    monkeypatch.setattr(bootstrap, "HOME", home)

    bootstrap.init_policies()  # must not raise

    assert not (home / "config" / "rag.yaml").exists()
