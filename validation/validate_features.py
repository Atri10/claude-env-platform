#!/usr/bin/env python3
"""
validate_features.py :: offline smoke suite for the extended feature set.

Runs ENTIRELY in an isolated temporary environment (own CLAUDE_ENV_HOME and
SQLite DB) so it never touches the real ledger, memory graph, or settings.
No model downloads, no network.

Covers: hooks (policy deny / secret-ask / installer), incident mode,
session-transcript ingestion, retrieval feedback, compliance report, session
replay, memory export/import redaction, policy simulation, budgets,
test-impact, doc-drift, context-pack, nightly analyst, approvals UI internals,
plugin manifests, CLI dispatcher coverage.

Usage:
    ~/.claude-env/venv/bin/python validation/validate_features.py
    # or: claude-env validate features
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# --- isolate BEFORE importing any platform module (get_db is a singleton) ---
_TMP = tempfile.mkdtemp(prefix="claude-env-features-")
os.environ["CLAUDE_ENV_HOME"] = _TMP
os.environ["CLAUDE_ENV_DSN"] = f"sqlite:///{_TMP}/state/test.db"
(Path(_TMP) / "state").mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(REPO))

GREEN, RED, YELLOW, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[0m"
_failures = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _failures
    if cond:
        print(f"{GREEN}PASS{RESET} {label}")
    else:
        _failures += 1
        print(f"{RED}FAIL{RESET} {label}" + (f" — {detail}" if detail else ""))


def _make_fixture() -> Path:
    """A tiny committed git repo so the repo-quality tools (policy-sim, test-impact,
    doc-drift, nightly analyst) run hermetically — independent of where this
    validator lives (source checkout vs the mirrored $CLAUDE_ENV_HOME, which has no
    tests/docs/.git). Contains a doc that references code, a source file, and a
    name-matching test."""
    fix = Path(_TMP) / "fixture"
    (fix / "src").mkdir(parents=True, exist_ok=True)
    (fix / "tests").mkdir(parents=True, exist_ok=True)
    (fix / "docs").mkdir(parents=True, exist_ok=True)
    (fix / "src" / "engine.py").write_text("def run():  # TODO: optimize later\n    return 42\n")
    (fix / "tests" / "test_engine.py").write_text(
        "from src.engine import run\n\n\ndef test_run():\n    assert run() == 42\n")
    (fix / "docs" / "guide.md").write_text(
        "# Guide\n\nThe entry point is `src/engine.py`.\n"
        "It also mentions `src/missing.py`, which does not exist.\n")
    (fix / "README.md").write_text("# Fixture\n\nSee `src/engine.py` for the core.\n")
    for args in (["init", "-q", str(fix)],
                 ["-C", str(fix), "config", "user.email", "v@example.com"],
                 ["-C", str(fix), "config", "user.name", "validator"],
                 ["-C", str(fix), "add", "-A"],
                 ["-C", str(fix), "commit", "-q", "-m", "fixture"]):
        subprocess.run(["git", *args], check=True, capture_output=True)
    return fix


def main() -> int:  # noqa: C901 - linear smoke checklist
    from lib.db import get_db
    db = get_db()
    db.apply_schema(str(REPO / "sql" / "001_schema.sql"),
                    str(REPO / "sql" / "002_retention.sql"),
                    str(REPO / "sql" / "003_extensions.sql"))
    check("schema (incl. 003 extensions) applies",
          bool(db.query_one("SELECT 1 FROM rag_chunk_feedback WHERE 0")) is False)

    env = os.environ.copy()

    # --- hooks -------------------------------------------------------------
    deny = subprocess.run(
        [sys.executable, str(REPO / "hooks" / "policy_hook.py")],
        input=json.dumps({"session_id": "v", "cwd": str(REPO), "tool_name": "Read",
                          "tool_input": {"file_path": str(REPO / ".env")}}),
        capture_output=True, text=True, env=env)
    check("policy hook denies blocked path",
          '"permissionDecision": "deny"' in deny.stdout, deny.stdout[:120])

    allow = subprocess.run(
        [sys.executable, str(REPO / "hooks" / "policy_hook.py")],
        input=json.dumps({"session_id": "v", "cwd": str(REPO), "tool_name": "Read",
                          "tool_input": {"file_path": str(REPO / "README.md")}}),
        capture_output=True, text=True, env=env)
    check("policy hook allows normal path (silent)", allow.stdout.strip() == "")

    ask = subprocess.run(
        [sys.executable, str(REPO / "hooks" / "policy_hook.py")],
        input=json.dumps({"session_id": "v", "cwd": str(REPO), "tool_name": "Write",
                          "tool_input": {"file_path": str(REPO / "x.py"),
                                         "content": "k=AKIAABCDEFGHIJKLMNOP"}}),
        capture_output=True, text=True, env=env)
    check("policy hook asks on secret write",
          '"permissionDecision": "ask"' in ask.stdout)

    inst = subprocess.run(
        [sys.executable, str(REPO / "hooks" / "install_hooks.py"),
         "--dry-run", "--settings", f"{_TMP}/settings.json"],
        capture_output=True, text=True, env=env)
    check("hook installer dry-run emits both hooks",
          "policy_hook.py" in inst.stdout and "audit_hook.py" in inst.stdout)

    # --- incident mode -------------------------------------------------------
    from security.policy_engine import PolicyEngine
    subprocess.run([sys.executable, str(REPO / "security" / "incident.py"),
                    "on", "--reason", "validation", "--by", "validator"],
                   capture_output=True, env=env)
    d = PolicyEngine.load(REPO).evaluate_path("README.md")
    check("incident mode blocks everything", d.action == "block" and d.rule == "incident")
    subprocess.run([sys.executable, str(REPO / "security" / "incident.py"),
                    "off", "--by", "validator"], capture_output=True, env=env)
    check("incident lift restores access",
          PolicyEngine.load(REPO).evaluate_path("README.md").action == "allow")

    # --- session ingestor + feedback ----------------------------------------
    # The ingestor only ingests sessions whose cwd is inside an ONBOARDED repo
    # (one with .claude/repo-policy.yaml) — un-onboarded repos are skipped by
    # design. So the fixture cwd must be a real onboarded repo dir, not a bare
    # made-up path; its repo-policy 'repo:' slug ('demo') is what keys the
    # session node's namespace and must match the 'demo' used by record_retrieved
    # and the later `know --repo demo` check.
    demo_repo = Path(_TMP) / "w" / "demo"
    (demo_repo / ".claude").mkdir(parents=True)
    (demo_repo / "src").mkdir(parents=True)
    (demo_repo / ".claude" / "repo-policy.yaml").write_text("repo: demo\ntier: 0\n")
    (demo_repo / "src" / "parser.py").write_text("def parse():\n    return 1\n")
    tdir = Path(_TMP) / "transcripts" / "p"
    tdir.mkdir(parents=True)
    lines = [
        {"type": "user", "cwd": str(demo_repo), "timestamp": "t",
         "message": {"role": "user", "content": "fix parser"}},
        {"type": "assistant", "cwd": str(demo_repo), "timestamp": "t",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "name": "Edit",
              "input": {"file_path": str(demo_repo / "src" / "parser.py")}}]}},
        {"type": "assistant", "cwd": str(demo_repo), "timestamp": "t",
         "message": {"role": "assistant", "content": [
             {"type": "text", "text": "done"}]}},
    ]
    (tdir / "sess1.jsonl").write_text("\n".join(json.dumps(x) for x in lines))
    from observability.feedback import record_retrieved, usage_boosts
    record_retrieved("demo", "main", "parser bug",
                     [{"chunk_id": "ck1", "file_path": "src/parser.py"}], "s")
    from memory.session_ingestor import ingest
    stats = ingest(Path(_TMP) / "transcripts")
    node = db.query_one("SELECT * FROM memory_nodes WHERE node_kind='session'")
    check("ingestor creates session node", stats["ingested"] == 1 and node is not None)
    check("ingestor correlates retrieval->edit ('used' signal)",
          stats["usage_signals"] == 1 and "ck1" in usage_boosts("demo", "main"))
    check("ingestor dedupes on re-run",
          ingest(Path(_TMP) / "transcripts")["ingested"] == 0)

    # --- compliance report + replay ------------------------------------------
    rep = subprocess.run([sys.executable, str(REPO / "audit" / "compliance_report.py"),
                          "--window", "1d", "--format", "json"],
                         capture_output=True, text=True, env=env)
    try:
        repd = json.loads(rep.stdout)
        check("compliance report (chain verified)", repd["chain"]["verified"] is True)
    except Exception as exc:
        check("compliance report (chain verified)", False, str(exc))
    rpl = subprocess.run([sys.executable, str(REPO / "audit" / "session_replay.py"),
                          "--list"], capture_output=True, text=True, env=env)
    check("session replay lists sessions", rpl.returncode == 0 and "events" in rpl.stdout)

    # --- memory sync ----------------------------------------------------------
    from memory.memory_manager import MemoryManager
    MemoryManager("proj-sync", actor="v").add_node(
        "episodic", "decision", "d1", {"note": "key AKIAABCDEFGHIJKLMNOP"})
    sync_out = Path(_TMP) / "sync.jsonl"
    subprocess.run([sys.executable, str(REPO / "memory" / "memory_sync.py"),
                    "export", "--namespace", "proj-sync", "--out", str(sync_out)],
                   capture_output=True, env=env)
    body = sync_out.read_text()
    check("memory export redacts secrets",
          "AKIA" not in body and "REDACTED" in body)
    # same-DB import must SKIP the existing node_id (idempotent)
    imp = subprocess.run([sys.executable, str(REPO / "memory" / "memory_sync.py"),
                          "import", "--in", str(sync_out), "--namespace", "proj-sync2"],
                         capture_output=True, text=True, env=env)
    check("memory import is idempotent (same node skipped)",
          "skipped 1" in imp.stdout)
    # simulate a teammate's file (different node ids) -> imports with remap
    remapped = Path(_TMP) / "sync-other.jsonl"
    remapped.write_text(sync_out.read_text().replace("mem-", "mem-peer"))
    imp2 = subprocess.run([sys.executable, str(REPO / "memory" / "memory_sync.py"),
                           "import", "--in", str(remapped), "--namespace", "proj-sync2"],
                          capture_output=True, text=True, env=env)
    ok_ns = db.query_one("SELECT namespace FROM memory_nodes WHERE node_id LIKE 'mem-peer%'")
    check("memory import remaps namespace",
          "imported nodes=1" in imp2.stdout and ok_ns
          and ok_ns["namespace"] == "proj-sync2")

    # --- repo-quality tools run against a hermetic fixture repo (not REPO, which
    #     when deployed is the $CLAUDE_ENV_HOME mirror with no tests/docs/.git) ---
    FIX = _make_fixture()

    # --- policy sim ------------------------------------------------------------
    cand = Path(_TMP) / "cand.yaml"
    cand.write_text("version: 1\ntier: 1\nrepo: fixture\n"
                    "deny:\n  paths: ['docs/**']\nallow:\n  paths: ['**']\n")
    sim = subprocess.run([sys.executable, str(REPO / "security" / "policy_sim.py"),
                          "simulate", str(FIX), "--candidate", str(cand),
                          "--format", "json"],
                         capture_output=True, text=True, env=env)
    try:
        simd = json.loads(sim.stdout)
        check("policy simulation reports newly-blocked docs",
              any(e["path"].startswith("docs/") for e in simd["newly_blocked"]))
    except Exception as exc:
        check("policy simulation reports newly-blocked docs", False, str(exc))

    # --- budgets ----------------------------------------------------------------
    (Path(_TMP) / "config").mkdir(exist_ok=True)
    (Path(_TMP) / "config" / "budgets.yaml").write_text(
        "warn_at: 0.8\nmonthly_usd:\n  default: 0\n  repos:\n    demo: 1\n")
    from datetime import datetime, timezone
    db.execute("INSERT INTO metrics_sessions (session_id,repo,started_at,est_cost_usd) "
               "VALUES ('b1','demo',?,2.0)",
               (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),))
    bud = subprocess.run([sys.executable, str(REPO / "observability" / "budgets.py"),
                          "--format", "json"],
                         capture_output=True, text=True, env=env)
    try:
        budd = json.loads(bud.stdout)
        check("budget exceed detected (exit 1)",
              bud.returncode == 1 and budd["overall"] == "EXCEEDED")
    except Exception as exc:
        check("budget exceed detected (exit 1)", False, str(exc))

    # --- repo quality tools -----------------------------------------------------
    ti = subprocess.run([sys.executable, str(REPO / "rag" / "test_impact.py"),
                         str(FIX), "--files", "src/engine.py",
                         "--format", "json"],
                        capture_output=True, text=True, env=env)
    try:
        tid = json.loads(ti.stdout)
        check("test-impact maps engine -> its test",
              any("test_engine" in t["file"] for t in tid["tests"]))
    except Exception as exc:
        check("test-impact maps engine -> its test", False, str(exc))

    dd = subprocess.run([sys.executable, str(REPO / "rag" / "doc_drift.py"),
                         str(FIX), "--format", "json"],
                        capture_output=True, text=True, env=env)
    try:
        ddd = json.loads(dd.stdout)
        check("doc-drift scans references", ddd["reference_pairs"] > 0)
    except Exception as exc:
        check("doc-drift scans references", False, str(exc))

    cp = subprocess.run([sys.executable, str(REPO / "rag" / "context_pack.py"),
                         str(REPO)], capture_output=True, text=True, env=env)
    check("context-pack generates a draft",
          "generated by claude-env context-pack" in cp.stdout
          and "## Layout" in cp.stdout)

    dg = subprocess.run([sys.executable,
                         str(REPO / "agents" / "analysts" / "nightly_analyst.py"),
                         str(FIX), "--no-memory"],
                        capture_output=True, text=True, env=env)
    check("nightly analyst writes a digest",
          dg.returncode == 0 and list((Path(_TMP) / "logs" / "digests").glob("*.md")))

    # --- approvals UI internals + know -------------------------------------------
    from audit.audit_logger import AuditLogger
    AuditLogger("ui-v", actor="devops").human_approval_request("devops", "apply", 1)
    from agents.orchestration import approvals_ui
    html_out = approvals_ui._pending_html()
    check("approvals UI renders pending rows",
          "appr-" in html_out and 'value="approve"' in html_out)

    kn = subprocess.run([sys.executable, str(REPO / "rag" / "pipelines" / "know.py"),
                         "parser", "--repo", "demo", "--format", "json"],
                        capture_output=True, text=True, env=env)
    try:
        knd = json.loads(kn.stdout)
        check("know fuses memory (finds ingested session)",
              any("parser" in m["name"] for m in knd["memory"]))
    except Exception as exc:
        check("know fuses memory (finds ingested session)", False, str(exc))

    # --- plugin manifests ----------------------------------------------------------
    # The plugin is a distribution artifact, not part of the runtime mirror — so it
    # is only present in a source checkout. Validate it there; skip when absent.
    plugin_dir = REPO / "claude-plugin"
    if plugin_dir.exists():
        plugin_ok = True
        for f in (".claude-plugin/plugin.json", "hooks/hooks.json", ".mcp.json"):
            try:
                json.loads((plugin_dir / f).read_text())
            except Exception:
                plugin_ok = False
        check("plugin manifests are valid JSON", plugin_ok)
    else:
        print(f"{YELLOW}SKIP{RESET} plugin manifests — claude-plugin/ not in this "
              f"tree (runtime mirror); validate from a source checkout")

    # --- CLI coverage -----------------------------------------------------------------
    cli = (REPO / "bin" / "claude-env").read_text()
    missing = [c for c in ("report", "replay", "incident", "know", "budget",
                           "rag-bench", "doc-drift", "test-impact", "hooks",
                           "memory-sync", "ingest-sessions", "context-pack",
                           "digest", "approvals-ui", "policy-sim")
               if f'"{c}"' not in cli]
    check("CLI dispatcher covers all new commands", not missing, str(missing))

    print()
    if _failures:
        print(f"{RED}{_failures} failure(s){RESET}")
        return 1
    print(f"{GREEN}all feature checks passed{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
