"""Static content checks for the git hook scripts that trigger RAG
re-indexing (scripts/post-commit, scripts/post-merge, scripts/post-checkout).

These don't execute the scripts (no real git working tree event to fire, and
the existing suite doesn't execute post-commit's bash either -- see
test_onboard_helpers.py, which tests the *installer*, and test_git_sync.py,
which tests the Python logic the scripts invoke). This asserts each script
dispatches to the right rag/git_sync.py event name, so a future edit can't
silently point post-merge at the wrong event without a test failing.
Run: pytest tests/ -q
"""
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (_ROOT / "scripts" / name).read_text()


def test_post_commit_dispatches_commit_event():
    text = _read("post-commit")
    assert "claude-env" in text
    assert "rag/git_sync.py" in text
    assert '"$REPO_ROOT" commit' in text
    assert "nohup" in text and "exit 0" in text


def test_post_merge_dispatches_merge_event():
    text = _read("post-merge")
    assert "claude-env" in text
    assert "rag/git_sync.py" in text
    assert '"$REPO_ROOT" merge' in text
    assert "ORIG_HEAD" in text
    assert "nohup" in text and "exit 0" in text


def test_post_checkout_dispatches_checkout_event():
    text = _read("post-checkout")
    assert "claude-env" in text
    assert "rag/git_sync.py" in text
    assert '"$REPO_ROOT" checkout "$1" "$2" "$3"' in text
    assert "nohup" in text and "exit 0" in text
