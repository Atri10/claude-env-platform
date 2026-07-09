#!/usr/bin/env python3
"""
claude-env :: shared repo-onboarding detection
File: lib/repo_policy.py
Purpose:
    Single place to answer "is this directory inside an onboarded repo, and if
    so what is its slug?" — by walking up from a cwd looking for
    <repo>/.claude/repo-policy.yaml. Used by anything that must not treat an
    un-onboarded repo as if it were governed: the audit hook (attribution) and
    the session ingestor (must-skip, no fallback).
"""
from __future__ import annotations

from pathlib import Path


def find_repo_policy(cwd: str | Path) -> tuple[Path, dict] | None:
    """Walk up from cwd looking for <root>/.claude/repo-policy.yaml.

    Returns (repo_root, policy_dict) for the first match, or None if no
    ancestor directory has a repo-policy.yaml (i.e. cwd is not inside an
    onboarded repo). A policy file that fails to parse is treated as absent
    (returns None) rather than raising — callers should not crash on a
    malformed policy file.
    """
    if not cwd:
        return None
    here = Path(cwd)
    for root in (here, *here.parents):
        pol = root / ".claude" / "repo-policy.yaml"
        if pol.exists():
            try:
                import yaml
                data = yaml.safe_load(pol.read_text()) or {}
            except Exception:
                return None
            return root, data
    return None


def repo_slug(cwd: str | Path, *, fallback_to_basename: bool = False) -> str | None:
    """The onboarded repo slug for cwd, or None if not onboarded.

    fallback_to_basename=True: if cwd is not inside an onboarded repo, return
    the directory basename anyway (used by the audit hook, where every session
    needs *some* repo label for attribution). fallback_to_basename=False
    (default): return None when not onboarded — used by anything that must
    skip un-onboarded repos entirely rather than guess a label for them.
    """
    if not cwd:
        return None
    found = find_repo_policy(cwd)
    if found is not None:
        root, data = found
        slug = data.get("repo")
        if slug:
            return str(slug)
        return root.name if fallback_to_basename else None
    return Path(cwd).name if fallback_to_basename else None
