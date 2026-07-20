"""
claude-env :: CLI Entry Point
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import click
import yaml

from claudenv._data import config_dir
from claudenv.adapters.config import get_config
from claudenv.adapters.config.providers import write_rag_config
from claudenv.application.audit import ComplianceReportGenerator, SessionReplay
from claudenv.application.observability import BudgetService, DashboardService, FeedbackService
from claudenv.application.onboarding import OnboardingService
from claudenv.di import get_container
from claudenv.ports.audit import IAuditRepository
from claudenv.ports.database import IDatabase


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")
@click.pass_context
def cli(ctx, verbose):
    """claude-env - Local-first AI governance platform."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------

def _dsn_to_db_path(dsn: str) -> Path:
    """Best-effort conversion of a database DSN to a filesystem path."""
    if dsn.startswith("sqlite:///"):
        return Path(dsn[len("sqlite:///"):])
    if dsn.startswith("sqlite://"):
        return Path(dsn[len("sqlite://"):])
    return Path(dsn)


def _is_initialized() -> bool:
    """Return True if the claude-env home has been provisioned (DB exists)."""
    config = get_config()
    db_path = _dsn_to_db_path(config.get_database_dsn())
    return db_path.exists()


def ensure_initialized(ctx):
    """Bail out with a helpful message (or auto-init) if the home is uninitialized.

    When the DB/schema is missing we prompt (TTY-aware, via click.confirm) to
    run ``claude-env init``; declining exits with an actionable error rather
    than a raw stack trace.
    """
    if _is_initialized():
        return

    if click.confirm("claude-env is not initialized. Run 'claude-env init' now?", default=True):
        ctx.invoke(init, force_config=False)
    else:
        click.echo(
            "Aborting: claude-env home is not initialized.\n"
            "Run `claude-env init` first, then retry this command.",
            err=True,
        )
        ctx.exit(1)


def _detect_branch(repo_root: str) -> str:
    """Resolve the indexing branch.

    Prefers ``default_branch`` recorded in ``.claude/repo-policy.yaml`` (written
    at onboarding), then falls back to ``git rev-parse --abbrev-ref HEAD``, and
    finally ``main``. Never hardcodes "main" as the sole source of truth.
    """
    policy = Path(repo_root) / ".claude" / "repo-policy.yaml"
    if policy.exists():
        try:
            data = yaml.safe_load(policy.read_text()) or {}
            branch = data.get("default_branch")
            if branch:
                return str(branch)
        except Exception:
            pass
    try:
        out = subprocess.run(
            ["git", "-C", repo_root, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        if out:
            return out
    except Exception:
        pass
    return "main"


def _rag_service(repo: str, branch: str):
    """Build a RagService bound to a specific repo/branch.

    Avoids the container's singleton ``RagService`` (which is wired for the
    "default" repo/branch) so that indexing and retrieval use a consistent
    repo key. Also sidesteps the broken ``IVectorStore`` DI factory by
    constructing the LanceDB store directly with the correct signature.
    """
    from claudenv.adapters.vector.lancedb import LanceDbRagRetriever, LanceDbVectorStore
    from claudenv.application.rag import RagIndexer, RagService
    from claudenv.domain.rag import BranchName, RepoSlug
    from claudenv.ports import (
        IEmbeddingProvider,
        ILanceDBConfig,
        IRagBookkeeping,
        IRAGConfig,
        IReranker,
    )

    container = get_container()
    lancedb_config = container.get(ILanceDBConfig)
    rag_config = container.get(IRAGConfig)
    rcfg = rag_config.get_rag_config()
    store = LanceDbVectorStore(lancedb_config.get_lancedb_path(), rcfg.embedding_dim)
    embedder = container.get(IEmbeddingProvider)
    reranker = container.get(IReranker)
    bk = container.get(IRagBookkeeping)
    indexer = RagIndexer(
        RepoSlug.from_string(repo), BranchName.from_string(branch),
        store, bk, embedder, rcfg,
    )
    retriever = LanceDbRagRetriever(store, embedder, reranker)
    return RagService(indexer, retriever, embedder, reranker, bookkeeping=bk)


def _model_setup_flow(ctx, *, interactive, model_path, pooling_type, embedding_dim, reranker_dir):
    """Interactive/CI model onboarding. Writes the deployed rag.yaml.

    Reuses the existing rag.yaml schema (embedding/reranker sub-keys) and the
    packaged default as the base, so only the user's choices are changed.
    """
    config = get_config()
    home = config.get_claude_env_home()
    deployed = Path(home) / "config" / "rag.yaml"
    if deployed.exists():
        base = yaml.safe_load(deployed.read_text()) or {}
    else:
        base = yaml.safe_load((config_dir() / "rag.yaml").read_text()) or {}
    base.setdefault("embedding", {})
    base.setdefault("reranker", {})
    emb = base["embedding"]
    rer = base["reranker"]

    # --- embedding model path (validate exists; allow empty/skip) ---
    if model_path is None:
        current = emb.get("model_path", "")
        if interactive:
            model_path = click.prompt(
                "Embedding model path (GGUF, empty to skip)",
                default=current, show_default=False,
            )
        else:
            model_path = current
    if model_path:
        p = Path(os.path.expanduser(str(model_path)))
        if not p.exists():
            if interactive:
                click.echo(f"  WARNING: model path does not exist: {p}")
                if not click.confirm("  Continue anyway?", default=False):
                    ctx.exit(1)
            else:
                click.echo(f"ERROR: embedding model path does not exist: {p}", err=True)
                ctx.exit(1)

    # --- pooling type ---
    if pooling_type is None:
        if interactive:
            pooling_type = click.prompt(
                "Pooling type", default=emb.get("pooling_type", "mean"),
                type=click.Choice(["mean", "cls", "last", "none"]), show_default=True,
            )
        else:
            pooling_type = emb.get("pooling_type", "mean")

    # --- embedding dimension ---
    if embedding_dim is None:
        if interactive:
            embedding_dim = click.prompt(
                "Embedding dimension", default=int(emb.get("embedding_dim", 768)),
                type=int, show_default=True,
            )
        else:
            embedding_dim = int(emb.get("embedding_dim", 768))

    # --- reranker ONNX dir (optional) ---
    if reranker_dir is None:
        if interactive:
            reranker_dir = click.prompt(
                "Reranker ONNX dir (optional, empty to disable)",
                default=rer.get("model_dir", ""), show_default=False,
            )
        else:
            reranker_dir = rer.get("model_dir", "")

    emb["model_path"] = str(model_path) if model_path else ""
    emb["pooling_type"] = pooling_type
    emb["embedding_dim"] = int(embedding_dim)
    rer["model_dir"] = str(reranker_dir) if reranker_dir else ""

    path = write_rag_config(home, base)
    click.echo(f"  Wrote RAG config -> {path}")
    return path


@cli.command()
@click.option("--force-config", is_flag=True,
              help="Overwrite existing deployed config files with the packaged defaults.")
@click.option("--interactive", "-i", is_flag=True,
              help="After initializing, run interactive model setup (embedding + reranker).")
@click.pass_context
def init(ctx, force_config, interactive):
    """Initialize the local claude-env home (one-time, idempotent).

    Creates $CLAUDE_ENV_HOME (default ~/.claude-env), applies the SQL schema,
    copies the packaged default config, and writes the genesis audit event.
    Replaces the old bootstrap.py for the data-provisioning step; pip installs
    the code, this provisions the runtime state. Safe to re-run.
    """
    import shutil

    from claudenv._data import config_dir, sql_dir
    from claudenv.adapters.audit import SqliteAuditLogger
    from claudenv.adapters.persistence import SQLiteDatabase
    from claudenv.domain.value_objects import SessionId

    config = get_config()
    home = Path(config.get_claude_env_home())

    verbose = ctx.obj.get("verbose", False)

    # 1. Directory skeleton.
    for sub in ("state", "config", "knowledge/lancedb"):
        (home / sub).mkdir(parents=True, exist_ok=True)

    click.echo(f"  [ok] home ready at {home}")

    # 2. Copy packaged default config (skip existing unless --force-config).
    copied, skipped = 0, 0

    for src in sorted(config_dir().glob("*")):

        if not src.is_file():
            continue

        dst = home / "config" / src.name

        if dst.exists() and not force_config:

            skipped += 1

            if verbose:
                click.echo(f"       skip {src.name} (exists)")

            continue

        shutil.copy2(src, dst)
        copied += 1

        if verbose:
            click.echo(f"       copy {src.name}")

    click.echo(f"  [ok] config: {copied} copied, {skipped} kept")

    # 3. Apply SQL schema (idempotent) against the state DB.
    dsn = config.get_database_dsn()

    db = SQLiteDatabase(dsn)
    schema_files = [str(p) for p in sorted(sql_dir().glob("*.sql"))]
    db.apply_schema(*schema_files)

    click.echo(f"  [ok] schema applied ({len(schema_files)} files) -> {dsn}")

    # 4. Genesis audit event (the logger writes GENESIS-chained on first append).
    logger = SqliteAuditLogger(
        db=db,
        session_id=SessionId.from_string("init"),
        actor="claude-env-init",
    )

    logger.agent_action(
        agent="claude-env-init",
        action="init",
        target=str(home),
        summary="claude-env home initialized",
    )

    result = logger.verify_chain()
    db.close()
    ok = result.ok

    click.echo(f"  [ok] audit ledger initialized (verify_chain: {'green' if ok else 'FAILED'})")

    if not ok:
        click.echo("\nInitialization completed but the audit chain did not verify!", err=True)
        sys.exit(1)

    click.echo("\nclaude-env is ready. Next: `claude-env onboard <repo>`.")

    if interactive:
        click.echo("\n=== Model setup ===")
        # Only prompt interactively when attached to a TTY; otherwise apply
        # non-interactive defaults so `init --interactive` never hangs in CI.
        _model_setup_flow(
            ctx, interactive=sys.stdin.isatty(),
            model_path=None, pooling_type=None,
            embedding_dim=None, reranker_dir=None,
        )


@cli.command()
@click.argument("repo_root", type=click.Path(exists=True, file_okay=False, resolve_path=True), required=False)
@click.option("--repo-name", help="Repo slug for namespaces")
@click.option("--tier", type=click.Choice(["0", "1", "2", "3"]), help="Privacy tier (0=public, 1=internal, 2=sensitive, 3=restricted)")
@click.option("--description", default="", help="Short description")
@click.option("--branch", help="Default branch")
@click.option("--interactive", "-i", is_flag=True, help="Force interactive mode (prompt for all values)")
@click.option("--yes", "-y", is_flag=True, help="Non-interactive, accept defaults (for CI)")
@click.option("--dry-run", is_flag=True, help="Print what would change")
@click.option("--no-template", is_flag=True, help="Skip CLAUDE.md and skills")
@click.option("--force-template", is_flag=True, help="Overwrite existing skills/agents")
@click.option("--force-policy", is_flag=True, help="Regenerate repo-policy.yaml")
@click.option("--no-post-commit", is_flag=True, help="Skip git hooks")
@click.pass_context
def onboard(ctx, repo_root, repo_name, tier, description, branch, interactive, yes, dry_run, no_template, force_template,
            force_policy, no_post_commit):
    """Onboard a repository to claude-env."""

    config = get_config()
    service = OnboardingService(config)

    # Auto-detect interactive mode: if stdin is a TTY and not --yes, default to interactive
    # Explicit --interactive overrides; explicit --yes forces non-interactive
    is_tty = sys.stdin.isatty()
    use_interactive = interactive or (is_tty and not yes)

    if use_interactive:
        repo_root, repo_name, tier, description, branch = _prompt_onboarding_inputs(
            repo_root, repo_name, tier, description, branch, yes, service
        )

    # Validate required repo_root after interactive prompts
    if not repo_root:
        click.echo("ERROR: Repository path is required", err=True)
        ctx.exit(1)

    result = service.onboard(
        repo_root=repo_root,
        slug=repo_name,
        tier=int(tier) if tier else None,
        branch=branch,
        description=description,
        dry_run=dry_run,
        force_policy=force_policy,
        force_template=force_template,
        no_template=no_template,
        no_post_commit=no_post_commit,
    )

    if dry_run:
        click.echo("DRY RUN - nothing written")
    else:
        click.echo(f"Onboarded: {result.slug} (tier {result.tier})")
        click.echo(f"  RAG table: {result.rag_table}")
        click.echo(f"  Memory ns: {result.memory_namespace} (isolated={result.memory_isolated})")


@cli.group()
def model():
    """Model configuration (embedding + reranker)."""
    pass


@model.command("setup")
@click.option("--model-path", default=None,
              help="Path to a local GGUF embedding model. Empty/skip to leave unconfigured.")
@click.option("--pooling-type", default=None,
              type=click.Choice(["mean", "cls", "last", "none"]),
              help="Pooling type for the embedding model.")
@click.option("--embedding-dim", default=None, type=int,
              help="Embedding vector dimension.")
@click.option("--reranker-dir", default=None,
              help="Directory with an ONNX cross-encoder reranker (empty to disable).")
@click.option("--yes", "-y", is_flag=True,
              help="Non-interactive: accept defaults / provided flags (for CI).")
@click.pass_context
def model_setup(ctx, model_path, pooling_type, embedding_dim, reranker_dir, yes):
    """Configure the embedding + reranker models (writes deployed rag.yaml)."""
    interactive = (not yes) and sys.stdin.isatty()
    _model_setup_flow(
        ctx, interactive=interactive,
        model_path=model_path, pooling_type=pooling_type,
        embedding_dim=embedding_dim, reranker_dir=reranker_dir,
    )


def _prompt_onboarding_inputs(
    repo_root: str | None,
    repo_name: str | None,
    tier: str | None,
    description: str,
    branch: str | None,
    non_interactive: bool,
    service: "OnboardingService",
) -> tuple[str, str | None, str | None, str, str | None]:
    """Prompt for missing onboarding inputs interactively."""
    from pathlib import Path

    from claudenv.domain.value_objects import Tier

    # If repo_root not provided, prompt for it
    if not repo_root:
        repo_root = click.prompt(
            "Repository path",
            type=click.Path(exists=True, file_okay=False, resolve_path=True),
        )

    repo_path = Path(repo_root).resolve()

    # Detect defaults
    detected_branch = branch or service._detect_branch(repo_path)
    detected_tier = tier
    if detected_tier is None:
        detected_tier_obj = service._detect_tier(repo_path)
        detected_tier = str(int(detected_tier_obj))
    detected_slug = repo_name or repo_path.name

    if non_interactive:
        # Non-interactive: use detected/provided values without prompting
        return repo_root, repo_name, tier, description, branch

    click.echo("\n=== claude-env Interactive Onboarding ===")
    click.echo(f"Repository: {repo_path}")
    click.echo()

    # Repo slug
    if not repo_name:
        repo_name = click.prompt(
            "Repo slug (namespace-safe identifier)",
            default=detected_slug,
            show_default=True,
        )

    # Privacy tier
    if tier is None:
        tier_labels = {
            "0": "public (open source, no secrets)",
            "1": "internal (company-internal, no customer data)",
            "2": "sensitive (PII, secrets, credentials)",
            "3": "restricted (regulated, classified)",
        }
        click.echo("Privacy tier:")
        for k, v in tier_labels.items():
            default_marker = " (default)" if k == detected_tier else ""
            click.echo(f"  {k} - {v}{default_marker}")
        tier = click.prompt(
            "Select tier [0-3]",
            default=detected_tier,
            show_default=True,
            type=click.Choice(["0", "1", "2", "3"]),
        )

    # Description
    if not description:
        description = click.prompt(
            "Short description (optional)",
            default=description,
            show_default=False,
        )

    # Branch
    if not branch:
        branch = click.prompt(
            "Default branch",
            default=detected_branch,
            show_default=True,
        )

    click.echo()
    click.echo("Summary:")
    click.echo(f"  Repo:     {repo_path}")
    click.echo(f"  Slug:     {repo_name}")
    click.echo(f"  Tier:     {tier} ({Tier(int(tier)).label})")
    click.echo(f"  Branch:   {branch}")
    if description:
        click.echo(f"  Desc:     {description}")
    click.echo()

    if not click.confirm("Proceed with onboarding?", default=True):
        click.echo("Aborted.")
        ctx = click.get_current_context()
        ctx.exit(0)

    return repo_root, repo_name, tier, description, branch


@cli.command()
@click.argument("repo_root", type=click.Path(exists=True, file_okay=False, resolve_path=True))
@click.pass_context
def scan(ctx, repo_root):
    """Preview what would be indexed (policy allow/block split)."""
    ensure_initialized(ctx)
    # Load the policy engine for this repo via the live PolicyService.
    from claudenv.domain.policy import PolicyService
    engine = PolicyService(get_config()).load_engine(repo_root)

    # Get candidate files
    import subprocess
    try:
        out = subprocess.run(
            ["git", "-C", repo_root, "ls-files"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        files = out.splitlines() if out else []
    except Exception:
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


@cli.command()
@click.argument("repo_root", type=click.Path(exists=True, file_okay=False, resolve_path=True))
@click.option("--branch", help="Branch to index (overrides auto-detection).")
@click.pass_context
def index(ctx, repo_root, branch):
    """Build full RAG index for a repository."""
    ensure_initialized(ctx)

    # Get repo slug from onboarding
    repo_policy_path = Path(repo_root) / ".claude" / "repo-policy.yaml"
    if not repo_policy_path.exists():
        click.echo("ERROR: Repo not onboarded. Run 'claude-env onboard' first.", err=True)
        ctx.exit(1)

    policy_data = yaml.safe_load(repo_policy_path.read_text())
    slug = policy_data.get("repo", Path(repo_root).name)
    branch = branch or _detect_branch(repo_root)

    service = _rag_service(slug, branch)

    # Get files
    out = subprocess.run(
        ["git", "-C", repo_root, "ls-files"],
        capture_output=True, text=True, timeout=10,
    ).stdout.strip()
    files = out.splitlines() if out else []

    click.echo(f"Indexing {len(files)} files (branch={branch})...")
    files_dict = {}
    for f in files:
        path = Path(repo_root) / f
        try:
            files_dict[f] = path.read_text(errors="ignore")
        except Exception:
            pass

    result = service.indexer.full_index(
        repo=slug, branch=branch, files=files_dict,
    )
    click.echo(f"Indexed: {result['files']} files, {result['chunks']} chunks")


@cli.group()
def validate():
    """Run validation checks."""
    pass


@validate.command("installation")
@click.pass_context
def validate_installation(ctx):
    """Validate full installation."""
    config = get_config()
    home = Path(config.get_claude_env_home())

    checks = [
        ("venv exists", (home / "venv" / "bin" / "python").exists()),
        ("config dir", (home / "config").exists()),
        ("global policy", (home / "config" / "global-policy.yaml").exists()),
        ("rag.yaml", (home / "config" / "rag.yaml").exists()),
        ("mcp-servers.json", (home / "config" / "mcp-servers.json").exists()),
        ("knowledge dir", (home / "knowledge" / "lancedb").exists()),
    ]

    all_pass = True
    for name, ok in checks:
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_pass = False
        click.echo(f"  [{status}] {name}")

    if all_pass:
        click.echo("\nAll checks passed!")
    else:
        click.echo("\nSome checks failed!", err=True)
        sys.exit(1)


@cli.group()
def policy_sim():
    """Dry-run a candidate policy before it goes live."""


@policy_sim.command("simulate")
@click.argument("repo_root", type=click.Path(exists=True, file_okay=False, resolve_path=True))
@click.option("--candidate", required=True,
              type=click.Path(exists=True, dir_okay=False, resolve_path=True),
              help="Candidate repo-policy YAML to evaluate (dry-run).")
@click.pass_context
def policy_sim_simulate(ctx, repo_root, candidate):
    """Show what a candidate repo policy would change (dry-run, nothing written)."""
    import yaml

    from claudenv.domain.policy import PolicyService

    candidate_data = yaml.safe_load(Path(candidate).read_text()) or {}
    service = PolicyService(get_config())
    result = service.simulate(repo_root, candidate_data)

    click.echo(f"# policy simulation — {result['repo']}")
    click.echo(
        f"tier: {result['tier_current']} -> {result['tier_candidate']}   "
        f"files: {result['files']}   "
        f"blocked: {result['blocked_current']} -> {result['blocked_candidate']}"
    )
    if result["newly_blocked"]:
        click.echo(f"\nNEWLY BLOCKED: {len(result['newly_blocked'])}")
        for f, _reason, rule in result["newly_blocked"][:20]:
            click.echo(f"  {f}  (deny: {rule})")
    if result["newly_allowed"]:
        click.echo(f"\nNEWLY ALLOWED: {len(result['newly_allowed'])}")
        for f, _reason, rule in result["newly_allowed"][:20]:
            click.echo(f"  {f}  (allow: {rule})")
    if result["changed_rule"]:
        click.echo(f"\nBLOCKED BY DIFFERENT RULE: {len(result['changed_rule'])}")
        for f, cur, can in result["changed_rule"][:20]:
            click.echo(f"  {f}  ({cur} -> {can})")


@cli.command()
@click.argument("repo_root", type=click.Path(exists=True, file_okay=False, resolve_path=True))
@click.argument("query")
@click.option("--branch", help="Branch to query (overrides auto-detection).")
@click.pass_context
def rag(ctx, repo_root, query, branch):
    """Test RAG retrieval for a repository."""
    ensure_initialized(ctx)

    repo_policy_path = Path(repo_root) / ".claude" / "repo-policy.yaml"
    if not repo_policy_path.exists():
        click.echo("ERROR: Repo not onboarded. Run 'claude-env onboard' first.", err=True)
        ctx.exit(1)
    policy_data = yaml.safe_load(repo_policy_path.read_text())
    slug = policy_data.get("repo", Path(repo_root).name)
    branch = branch or _detect_branch(repo_root)

    service = _rag_service(slug, branch)
    results = service.search(
        repo=slug, branch=branch, query=query, top_k=5,
    )

    if not results:
        click.echo("No results")
        return

    for r in results:
        click.echo(f"\n[{r.rank}] {r.chunk.file_path}:{r.chunk.start_line}-{r.chunk.end_line} (score: {r.score:.3f})")
        click.echo(f"    {r.chunk.text[:200]}...")


@cli.command()
@click.argument("repo_root", type=click.Path(exists=True, file_okay=False, resolve_path=True))
@click.pass_context
def hooks(ctx, repo_root):
    """Install native-tool governance hooks."""
    from claudenv.adapters.hooks import HookInstaller

    result = HookInstaller(repo_root).install()
    click.echo(json.dumps(result, indent=2))
    if not result.get("installed"):
        sys.exit(1)


@cli.command()
@click.option("--window", default="7d", help="Time window (e.g., 7d, 30d)")
@click.option("--repo", help="Filter by repo")
@click.option("--format", type=click.Choice(["markdown", "csv"]), default="markdown")
@click.option("--out", type=click.Path(), help="Output file")
@click.pass_context
def report(ctx, window, repo, format, out):
    """Generate compliance report."""
    container = get_container()

    generator = ComplianceReportGenerator(container.get(IDatabase), container.get(IAuditRepository))

    result = generator.generate(window=window, repo=repo, format=format)

    if out:
        Path(out).write_text(result)
        click.echo(f"Report written to {out}")
    else:
        click.echo(result)


@cli.command()
@click.option("--list", "list_sessions", is_flag=True, help="List recent sessions")
@click.argument("session_id", required=False)
@click.pass_context
def replay(ctx, list_sessions, session_id):
    """Replay session forensics."""
    container = get_container()

    replay = SessionReplay(container.get(IDatabase))

    if list_sessions:
        sessions = replay.list_recent(20)
        for s in sessions:
            click.echo(f"  {s['session_id']}  {s['events']} events  {s['first']} -> {s['last']}  actors={s['actors']}")
    elif session_id:
        timeline = replay.get_timeline(session_id)
        for step in timeline:
            click.echo(f"  [{step['ts']}] #{step['event_id']} {step['type']} [{step['actor']}] {step['summary']}")
    else:
        click.echo("Use --list or provide a session_id")


@cli.command()
@click.argument("action", type=click.Choice(["on", "off", "status"]))
@click.option("--reason", default="", help="Reason for incident mode")
@click.option("--by", default="operator", help="Operator identity")
@click.pass_context
def incident(ctx, action, reason, by):
    """Incident mode kill switch."""
    from claudenv.domain.incident import IncidentState, clear_incident, write_incident

    config = get_config()
    home = Path(config.get_claude_env_home())

    if action == "on":
        # Fail-closed: deny any in-flight approvals before arming incident mode
        # so a pending human_approvals request can't be actioned while locked
        # down. Built directly from the DB/audit ports (not the DI container) so
        # it still works when other services (e.g. the embedder) are unconfigured.
        try:
            from claudenv.adapters.audit import SqliteAuditLogger
            from claudenv.adapters.persistence import SQLiteDatabase
            from claudenv.application.approval import ApprovalGate
            from claudenv.domain.value_objects import SessionId

            db = SQLiteDatabase(config.get_database_dsn())
            audit = SqliteAuditLogger(
                db, SessionId.from_string("incident-mode"), actor="incident-mode",
                repo="", tier=None,
            )
            gate = ApprovalGate(audit, db)
            for row in gate.list_open():
                gate.resolve(row["request_id"], approved=False, decided_by="INCIDENT")
            db.close()
        except Exception:
            # Best-effort: incident mode must still arm even if the gate is down.
            pass
        write_incident(reason=reason, by=by, home=home)
        click.echo(f"Incident mode ON: {reason}")
    elif action == "off":
        clear_incident(home=home)
        click.echo("Incident mode OFF")
    else:
        state = IncidentState.read(home=home)
        if state.active:
            click.echo(
                f"Incident mode ACTIVE since {state.since} by {state.by}: {state.reason}"
            )
        else:
            click.echo("Incident mode OFF")


@cli.command()
@click.pass_context
def services(ctx):
    """List running local UI services."""
    config = get_config()
    home = Path(config.get_claude_env_home())
    registry_file = home / "state" / "services.json"

    if not registry_file.exists():
        click.echo("No services running")
        return

    import json
    data = json.loads(registry_file.read_text())
    for name, info in data.items():
        click.echo(f"  {name}: {info['url']} (pid={info.get('pid')})")


@cli.command()
@click.option("--repo", help="Filter to one repo")
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text")
@click.pass_context
def budget(ctx, repo, fmt):
    """Check month-to-date spend against configured budgets (advisory).

    Exits non-zero only when a budget is EXCEEDED, so a CI job or shell prompt
    can gate on it. Warnings alone still exit 0.
    """
    svc = get_container().get(BudgetService)
    result = svc.evaluate(repo=repo)

    if fmt == "json":
        import json
        click.echo(json.dumps(result.to_dict(), indent=2))
    else:
        if not result.repos:
            click.echo("No session cost data yet.")
        else:
            click.echo(f"{'repo':<24} {'sessions':>8} {'spent':>10} {'budget':>10} {'pct':>6}  status")
            for r in result.repos:
                budget_s = f"${r.budget_usd:.2f}" if r.budget_usd else "-"
                pct_s = f"{r.pct * 100:.0f}%" if r.pct is not None else "-"
                click.echo(
                    f"{r.repo:<24} {r.sessions:>8} ${r.spent_usd:>8.2f} {budget_s:>10} "
                    f"{pct_s:>6}  {r.status.value}"
                )
        click.echo(f"\noverall: {result.overall.value}")

    if result.overall.value == "EXCEEDED":
        sys.exit(1)


@cli.command()
@click.option("--window", default="30d", help="Time window (e.g. 24h, 7d, 30d)")
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text")
@click.pass_context
def dashboard(ctx, window, fmt):
    """Read-only operational summary (costs, latency, retrieval quality, violations)."""
    svc = get_container().get(DashboardService)
    summary = svc.summary(window=window)

    if fmt == "json":
        import json
        click.echo(json.dumps(summary, indent=2, default=str))
        return

    click.echo(f"== claude-env dashboard (window={window}) ==\n")

    click.echo("Top session costs:")
    for row in summary["top_session_costs"]:
        click.echo(f"  {row['repo']:<20} ${row['spent_usd']:>8}  {row['session_id']}")

    click.echo("\nCost by repo (total):")
    for row in summary.get("cost_by_repo", []):
        click.echo(f"  {row.repo:<20} ${row.spent_usd:>8}  ({row.sessions} sessions)")

    click.echo("\nLatency by component (ms):")
    for component, p in summary["latency"].items():
        click.echo(f"  {component:<20} p50={p['p50']:.0f} p95={p['p95']:.0f} max={p['max']:.0f} (n={p['count']})")

    click.echo("\nRetrieval quality (mean top-1 by repo):")
    for row in summary["retrieval_quality"]:
        click.echo(f"  {row['repo']:<20} {row['mean_top1']}  (n={row['queries']})")

    click.echo("\nRecent policy violations:")
    for row in summary["policy_violations"]:
        click.echo(f"  [{row['ts']}] {row['decision']} {row['path']} ({row['rule']})")

    click.echo("\nSecurity events:")
    for row in summary["security_events"]:
        click.echo(f"  {row['severity']:<8} {row['category']:<18} {row['n']}")

    click.echo("\nOpen approvals:")
    for row in summary["open_approvals"]:
        click.echo(f"  {row['request_id']}  {row['agent']}  {row['action']}")


@cli.command()
@click.option("--repo", help="Filter to one repo")
@click.pass_context
def feedback(ctx, repo):
    """Show RAG retrieval feedback stats (retrieved/used counts, top files)."""
    svc = get_container().get(FeedbackService)
    stats = svc.stats(repo=repo)
    click.echo(f"retrieved: {stats.get('retrieved', 0)}")
    click.echo(f"used:      {stats.get('used', 0)}")
    top = stats.get("top_used_files", [])
    if top:
        click.echo("top used files:")
        for path, n in top:
            click.echo(f"  {n:>4}  {path}")


if __name__ == "__main__":
    cli()
