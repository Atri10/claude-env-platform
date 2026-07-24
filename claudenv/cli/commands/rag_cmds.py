"""claude-env :: CLI - scan, index, rag commands."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import click
import yaml

from claudenv.adapters.config import get_config
from claudenv.cli.context import _detect_branch, _rag_service, ensure_initialized
from claudenv.domain.rag import BranchName, RepoSlug

logger = logging.getLogger(__name__)


@click.command("scan")
@click.argument("repo_root", type=click.Path(exists=True, file_okay=False, resolve_path=True))
@click.pass_context
def scan(ctx, repo_root):
    """Preview what would be indexed (policy allow/block split)."""
    ensure_initialized(ctx)
    logger.debug("[flow] scan: start repo_root=%s", repo_root)

    from claudenv.application.policy import PolicyService
    engine = PolicyService(get_config()).load_engine(repo_root)

    try:
        out = subprocess.run(
            ["git", "-C", repo_root, "ls-files"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        files = out.splitlines() if out else []
    except Exception:
        logger.warning("git ls-files failed; falling back to rglob", exc_info=True)
        files = [str(p.relative_to(repo_root)) for p in Path(repo_root).rglob("*") if p.is_file()]

    allowed, blocked = [], []
    for f in files:
        decision = engine.evaluate_path(f)
        (allowed if decision.is_allowed else blocked).append((f, decision.reason))

    click.echo(f"Candidates: {len(files)}")
    click.echo(f"Allowed: {len(allowed)}")
    click.echo(f"Blocked: {len(blocked)}")
    if blocked:
        click.echo("\nBlocked:")
        for path, reason in blocked[:20]:
            click.echo(f"  {path}: {reason}")
        if len(blocked) > 20:
            click.echo(f"  ... and {len(blocked) - 20} more")


@click.command("index")
@click.argument("repo_root", type=click.Path(exists=True, file_okay=False, resolve_path=True))
@click.option("--branch", help="Branch to index (overrides auto-detection).")
@click.option("--full", is_flag=True, help="Force full reindex (ignore prior state).")
@click.pass_context
def index(ctx, repo_root, branch, full):
    """Build RAG index for a repository (incremental by default)."""
    ensure_initialized(ctx)
    logger.debug("[flow] index: start repo_root=%s branch=%s full=%s", repo_root, branch, full)

    repo_policy_path = Path(repo_root) / ".claude" / "repo-policy.yaml"
    if not repo_policy_path.exists():
        click.echo("ERROR: Repo not onboarded. Run 'claude-env onboard' first.", err=True)
        ctx.exit(1)

    policy_data = yaml.safe_load(repo_policy_path.read_text())
    slug = policy_data.get("repo", Path(repo_root).name)
    branch = branch or _detect_branch(repo_root)
    service = _rag_service(slug, branch)

    commit = "0"
    try:
        commit = subprocess.run(
            ["git", "-C", repo_root, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except Exception:
        pass

    bookkeeping = service.bookkeeping
    prior_state = None if full else bookkeeping.get_index_state(
        RepoSlug.from_string(slug), BranchName.from_string(branch),
    )

    if prior_state and prior_state.last_commit and not full:
        last_commit = prior_state.last_commit
        changed_files = {}
        try:
            diff = subprocess.run(
                ["git", "-C", repo_root, "diff", "--name-status", last_commit, "HEAD"],
                capture_output=True, text=True, timeout=15,
            ).stdout.strip()
            for line in diff.splitlines():
                if not line:
                    continue
                parts = line.split("\t")
                status, path = parts[0], parts[-1]
                full_path = Path(repo_root) / path
                if status == "D":
                    changed_files[path] = None
                elif full_path.exists() and full_path.is_file():
                    changed_files[path] = full_path.read_text(errors="ignore")
        except Exception:
            logger.warning("git diff failed; falling back to full index", exc_info=True)
            prior_state = None

    if prior_state and changed_files:
        click.echo(f"Indexing {len(changed_files)} changed files (branch={branch})...")
        result = service.indexer.incremental_index(
            repo=slug, branch=branch, commit=commit, changed=changed_files,
        )
        click.echo(f"Indexed: {result['files']} files, {result['chunks']} chunks")
    elif prior_state and not changed_files:
        click.echo(f"Index up to date (branch={branch}, commit={commit[:8]}).")
    else:
        out = subprocess.run(
            ["git", "-C", repo_root, "ls-files"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        all_files = out.splitlines() if out else []
        click.echo(f"Indexing {len(all_files)} files (branch={branch})...")
        files_dict = {}
        with click.progressbar(all_files, label="Reading files") as bar:
            for f in bar:
                p = Path(repo_root) / f
                try:
                    files_dict[f] = p.read_text(errors="ignore")
                except Exception:
                    logger.warning("could not read file for indexing; skipping %s", f, exc_info=True)
        result = service.indexer.full_index(repo=slug, branch=branch, files=files_dict)
        click.echo(f"Indexed: {result['files']} files, {result['chunks']} chunks")
        logger.debug("[flow] index: complete files=%s chunks=%s", result['files'], result['chunks'])


@click.command("rag")
@click.argument("repo_root", type=click.Path(exists=True, file_okay=False, resolve_path=True))
@click.argument("query")
@click.option("--branch", help="Branch to query (overrides auto-detection).")
@click.pass_context
def rag(ctx, repo_root, query, branch):
    """Test RAG retrieval for a repository."""
    ensure_initialized(ctx)
    logger.debug("[flow] rag: start repo_root=%s branch=%s", repo_root, branch)

    repo_policy_path = Path(repo_root) / ".claude" / "repo-policy.yaml"
    if not repo_policy_path.exists():
        click.echo("ERROR: Repo not onboarded. Run 'claude-env onboard' first.", err=True)
        ctx.exit(1)
    policy_data = yaml.safe_load(repo_policy_path.read_text())
    slug = policy_data.get("repo", Path(repo_root).name)
    branch = branch or _detect_branch(repo_root)

    service = _rag_service(slug, branch)
    results = service.search(repo=slug, branch=branch, query=query, top_k=5)

    if not results:
        click.echo("No results")
        return
    for r in results:
        click.echo(f"\n[{r.rank}] {r.chunk.file_path}:{r.chunk.start_line}-{r.chunk.end_line} (score: {r.score:.3f})")
        click.echo(f"    {r.chunk.text[:200]}...")
