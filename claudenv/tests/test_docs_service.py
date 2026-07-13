"""
Tests for claudenv.application.docs.DocsService.

The refactor left every method here as a total no-op (list_dependencies() ->
[], get_readme() -> None, etc., regardless of what actually existed on
disk) and dropped the pre-refactor documentation MCP server's two tools
entirely: local docs-corpus search and the tier-gated, poison-screened
external fetch (the platform's only sanctioned network egress -- see
CLAUDE.md's "No network egress by default" invariant).
"""
from __future__ import annotations

from claudenv.application.docs import DocsService
from claudenv.domain.value_objects import Tier


class TestGetReadme:
    def test_reads_readme_from_repo_root(self, tmp_path):
        (tmp_path / "README.md").write_text("# Hello\n")
        service = DocsService(tmp_path, Tier.INTERNAL)
        assert service.get_readme() == "# Hello\n"

    def test_returns_none_when_no_readme(self, tmp_path):
        service = DocsService(tmp_path, Tier.INTERNAL)
        assert service.get_readme() is None


class TestGetFile:
    def test_reads_file_under_repo_root(self, tmp_path):
        (tmp_path / "docs.txt").write_text("hello")
        service = DocsService(tmp_path, Tier.INTERNAL)
        assert service.get_file("docs.txt") == "hello"

    def test_blocks_path_traversal_outside_repo_root(self, tmp_path):
        secret = tmp_path.parent / "secret.txt"
        secret.write_text("top secret")
        service = DocsService(tmp_path, Tier.INTERNAL)
        assert service.get_file("../secret.txt") is None

    def test_missing_file_returns_none(self, tmp_path):
        service = DocsService(tmp_path, Tier.INTERNAL)
        assert service.get_file("nope.txt") is None


class TestListDependencies:
    def test_parses_requirements_txt(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("requests==2.31.0\n# comment\npyyaml>=6.0\n")
        service = DocsService(tmp_path, Tier.INTERNAL)
        deps = service.list_dependencies()
        names = {d["name"] for d in deps}
        assert names == {"requests", "pyyaml"}

    def test_no_manifest_returns_empty(self, tmp_path):
        service = DocsService(tmp_path, Tier.INTERNAL)
        assert service.list_dependencies() == []


class TestFindReference:
    def test_finds_class_and_function_definitions(self, tmp_path):
        (tmp_path / "app.py").write_text("class Widget:\n    pass\n\ndef make_widget():\n    return Widget()\n")
        service = DocsService(tmp_path, Tier.INTERNAL)
        refs = service.find_reference("widget", kind="any")
        kinds = {(r["kind"], r["name"]) for r in refs}
        assert ("class", "Widget") in kinds
        assert ("function", "make_widget") in kinds


class TestSearchLocal:
    def test_finds_matching_doc(self, tmp_path):
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "notes.md").write_text("audit ledger hash chain notes")
        service = DocsService(tmp_path, Tier.INTERNAL, docs_dir=docs_dir)
        result = service.search_local("audit ledger")
        assert "notes.md" in result

    def test_no_corpus_configured(self, tmp_path):
        service = DocsService(tmp_path, Tier.INTERNAL, docs_dir=None)
        assert service.search_local("anything") == "(no local docs corpus)"


class TestFetchAllowed:
    def test_public_and_internal_allowed(self, tmp_path):
        assert DocsService(tmp_path, Tier.PUBLIC).fetch_allowed
        assert DocsService(tmp_path, Tier.INTERNAL).fetch_allowed

    def test_sensitive_and_restricted_denied(self, tmp_path):
        assert not DocsService(tmp_path, Tier.SENSITIVE).fetch_allowed
        assert not DocsService(tmp_path, Tier.RESTRICTED).fetch_allowed
