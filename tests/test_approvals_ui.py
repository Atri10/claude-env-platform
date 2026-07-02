"""Unit coverage for the approvals UI pure helpers (no DB / no server).
Run: pytest tests/ -q"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agents.orchestration.approvals_ui as ui


def test_command_strips_prefix():
    assert ui._command_of("terminal.run: go test ./...") == "go test ./..."
    assert ui._command_of("some other action") == "some other action"


def test_tier_pill():
    assert 'tier 0' in ui._tier_pill(0) and 'public' in ui._tier_pill(0)
    assert 'tier 3' in ui._tier_pill(3) and 'restricted' in ui._tier_pill(3)
    assert 'tier ?' in ui._tier_pill(None)          # unknown tier is safe


def test_ago_formats():
    assert ui._ago(None) == ""
    assert ui._ago("2020-01-01T00:00:00.000Z").endswith("d ago")   # long past -> days


def test_default_decider_has_user_and_host():
    d = ui._default_decider()
    assert "@" in d and len(d) > 2                    # user@host, auto-detected
