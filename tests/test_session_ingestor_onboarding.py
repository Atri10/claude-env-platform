"""Coverage for the onboarding gate in memory/session_ingestor.py. Run: pytest tests/ -q

Regression for the gap left by the repo-local hooks move: session_ingestor.py
walks ~/.claude/projects/*.jsonl directly and does NOT go through the
per-repo native-tool hooks, so narrowing hook install to onboarded repos does
nothing to it. Before this fix, every session on the machine — onboarded or
not — was ingested into the memory graph, keyed by directory basename. This
guards that un-onboarded repos are skipped entirely (no memory node, no
guessed label) while onboarded repos are ingested and correctly keyed by the
repo-policy.yaml slug (not the directory basename, which may differ).
"""
import importlib
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


def _fresh_db():
    """Point lib.db at a fresh temp sqlite and reset its singleton."""
    dsn = "sqlite:///" + tempfile.mktemp(suffix=".db")
    os.environ["CLAUDE_ENV_DSN"] = dsn
    import lib.db as db
    importlib.reload(db)
    d = db.get_db()
    d.apply_schema(str(_ROOT / "sql" / "001_schema.sql"))
    d.apply_schema(str(_ROOT / "sql" / "003_extensions.sql"))
    return d


def _write_transcript(path: Path, cwd: str, n_events: int = 4) -> None:
    lines = []
    lines.append(json.dumps({
        "cwd": cwd, "timestamp": "2026-01-01T00:00:00Z",
        "message": {"role": "user", "content": "add a widget"},
    }))
    for i in range(n_events - 1):
        lines.append(json.dumps({
            "cwd": cwd, "timestamp": "2026-01-01T00:01:00Z",
            "message": {"role": "assistant", "content": [
                {"type": "text", "text": "done"},
                {"type": "tool_use", "name": "Write",
                 "input": {"file_path": f"{cwd}/widget_{i}.py"}},
            ]},
        }))
    path.write_text("\n".join(lines) + "\n")


def test_un_onboarded_repo_session_is_skipped_not_ingested(tmp_path):
    db = _fresh_db()
    from memory.session_ingestor import ingest

    repo_dir = tmp_path / "some-repo"
    repo_dir.mkdir()  # NOTE: no .claude/repo-policy.yaml -> not onboarded

    transcripts = tmp_path / "projects" / "-some-munged-path"
    transcripts.mkdir(parents=True)
    _write_transcript(transcripts / "abc123.jsonl", cwd=str(repo_dir))

    stats = ingest(tmp_path / "projects", dry_run=False)

    assert stats["ingested"] == 0
    assert stats["not_onboarded"] == 1
    assert stats["skipped"] == 1

    # dedupe bookkeeping recorded (so we don't rescan every night), but with
    # no node_id and no repo label — it was never ingested into memory.
    row = db.query_one(
        "SELECT repo, node_id FROM session_ingest_state WHERE session_id=?",
        ("abc123",))
    assert row is not None
    assert row["node_id"] is None
    assert row["repo"] is None

    # and critically: no memory node exists anywhere for this session
    nodes = db.query("SELECT * FROM memory_nodes")
    assert len(nodes) == 0


def test_onboarded_repo_session_is_ingested_with_policy_slug(tmp_path):
    db = _fresh_db()
    from memory.session_ingestor import ingest

    repo_dir = tmp_path / "checkout-dir-name"  # basename deliberately != slug
    (repo_dir / ".claude").mkdir(parents=True)
    (repo_dir / ".claude" / "repo-policy.yaml").write_text("repo: acme-billing\ntier: 1\n")

    transcripts = tmp_path / "projects" / "-onboarded-path"
    transcripts.mkdir(parents=True)
    _write_transcript(transcripts / "def456.jsonl", cwd=str(repo_dir))

    stats = ingest(tmp_path / "projects", dry_run=False)

    assert stats["ingested"] == 1
    assert stats["not_onboarded"] == 0

    row = db.query_one(
        "SELECT repo, node_id FROM session_ingest_state WHERE session_id=?",
        ("def456",))
    assert row is not None
    assert row["node_id"] is not None
    # keyed by the repo-policy.yaml slug, NOT the directory basename
    assert row["repo"] == "acme-billing"

    nodes = db.query("SELECT namespace FROM memory_nodes")
    assert len(nodes) == 1
    assert nodes[0]["namespace"] == "proj-acme-billing"


def test_repo_slug_helper_no_fallback_when_not_onboarded(tmp_path):
    from lib.repo_policy import repo_slug

    plain_dir = tmp_path / "random-checkout"
    plain_dir.mkdir()
    assert repo_slug(str(plain_dir), fallback_to_basename=False) is None
    assert repo_slug(str(plain_dir), fallback_to_basename=True) == "random-checkout"


def test_repo_slug_helper_walks_up_and_reads_policy(tmp_path):
    from lib.repo_policy import repo_slug

    repo_dir = tmp_path / "myrepo"
    nested = repo_dir / "src" / "pkg"
    nested.mkdir(parents=True)
    (repo_dir / ".claude").mkdir()
    (repo_dir / ".claude" / "repo-policy.yaml").write_text("repo: my-slug\n")

    # from a nested subdirectory, still finds the ancestor's policy
    assert repo_slug(str(nested), fallback_to_basename=False) == "my-slug"


def test_audit_hook_repo_slug_still_falls_back_to_basename(tmp_path):
    """audit_hook._repo_slug now delegates to lib.repo_policy.repo_slug, but
    its own contract (fallback to basename for attribution) must be unchanged
    — the audit hook only ever fires inside a repo that installed it, so an
    un-onboarded cwd there would be a hook-install bug, not the normal case
    session_ingestor guards against."""
    sys.path.insert(0, str(_ROOT / "hooks"))
    import audit_hook
    importlib.reload(audit_hook)

    plain_dir = tmp_path / "some-checkout"
    plain_dir.mkdir()
    assert audit_hook._repo_slug(str(plain_dir)) == "some-checkout"

    repo_dir = tmp_path / "checkout-name"
    (repo_dir / ".claude").mkdir(parents=True)
    (repo_dir / ".claude" / "repo-policy.yaml").write_text("repo: real-slug\n")
    assert audit_hook._repo_slug(str(repo_dir)) == "real-slug"
