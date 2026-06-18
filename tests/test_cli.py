"""Tests for the contextweaver CLI (__main__.py).

Most tests exercise a real subcommand via subprocess, creating any needed
temp files on the fly.  A few drive the Typer app in-process via ``CliRunner``
where a deterministic failure must be simulated (e.g. the ``verify`` failure
path, issue #706).
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from contextweaver.__main__ import app
from contextweaver._verify import _VerifyCheck


def _run(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "contextweaver", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=cwd,
        env=env,
    )


# ------------------------------------------------------------------
# No-args / help
# ------------------------------------------------------------------


def test_no_args_prints_help() -> None:
    result = _run()
    # Typer exits with code 2 when invoked without a subcommand (Click's
    # ``UsageError`` convention) while still printing the help banner.  The
    # argparse predecessor exited 0; the v0.5 CLI rewrite (#221) adopts
    # Typer's convention.  Accept either to keep the test useful as a
    # smoke check regardless of which framework is in use.
    assert result.returncode in (0, 2)
    output = ((result.stdout or "") + (result.stderr or "")).lower()
    assert "contextweaver" in output or "usage" in output


# ------------------------------------------------------------------
# demo
# ------------------------------------------------------------------


def test_demo_runs_to_completion() -> None:
    result = _run("demo")
    assert result.returncode == 0
    assert "demo" in result.stdout.lower()
    assert "complete" in result.stdout.lower() or "loaded" in result.stdout.lower()


def test_demo_help_lists_all_scenarios() -> None:
    result = _run("demo", "--help")
    assert result.returncode == 0
    out = result.stdout
    for name in (
        "default",
        "large-catalog",
        "huge-tool-output",
        "mcp-gateway",
        "mcp-gateway-full",
    ):
        assert name in out, f"--help missing scenario {name!r}"


def test_demo_default_scenario_explicit_flag() -> None:
    result = _run("demo", "--scenario", "default")
    assert result.returncode == 0
    assert "default scenario" in result.stdout.lower()
    assert "demo complete" in result.stdout.lower()


def test_demo_large_catalog_scenario() -> None:
    result = _run("demo", "--scenario", "large-catalog")
    assert result.returncode == 0
    out = result.stdout
    # Pin only invariants the user cares about per the demo spec.
    assert "1000 tools" in out, "catalog size header missing"
    assert "Cards exposed to model:" in out, "card-count header missing"
    assert "Selected candidate IDs:" in out, "selected-IDs line missing"
    assert "NO full schemas" in out, "compact-card reassurance missing"
    assert "Demo complete" in out


def test_demo_huge_tool_output_scenario() -> None:
    result = _run("demo", "--scenario", "huge-tool-output")
    assert result.returncode == 0
    out = result.stdout
    assert "Raw tool output:" in out, "raw-size header missing"
    assert "After context firewall" in out, "firewall section missing"
    assert "Artifact ref:" in out, "artifact-ref line missing"
    assert "Artifact store" in out, "artifact-store section missing"
    # The firewall must actually shrink the prompt-side text.
    assert "Token savings vs raw:" in out
    # 120 rows of synthetic data; raw must be larger than the summary text.
    assert "9689 bytes raw" in out or "bytes raw" in out
    assert "Demo complete" in out


def test_demo_mcp_gateway_scenario() -> None:
    result = _run("demo", "--scenario", "mcp-gateway")
    assert result.returncode == 0
    out = result.stdout
    # The four narrative steps the spec asks for: meta-tools, browse, execute, view.
    assert "Meta-tools the gateway advertises" in out
    assert "tool_browse" in out
    assert "tool_execute" in out
    assert "stored out-of-band" in out, "firewall artifact not surfaced"
    # The github stub must successfully execute end-to-end.
    assert "status=ok" in out
    assert "Demo complete" in out


def test_demo_mcp_gateway_full_scenario() -> None:
    """The new mcp-gateway-full scenario (#264) runs the 60-tool architecture."""
    result = _run("demo", "--scenario", "mcp-gateway-full")
    assert result.returncode == 0
    out = result.stdout
    # The full architecture prints the metrics block with the documented fields.
    assert "catalog_tools" in out
    assert "exposed_choice_cards" in out
    assert "firewall_reduction_pct" in out
    # Top-1 routing must still pick bigquery.run_query (the marquee path).
    assert "bigquery.run_query" in out


def test_demo_unknown_scenario_rejected() -> None:
    result = _run("demo", "--scenario", "nope")
    # Typer/Click returns exit-code 2 for invalid choice values.
    assert result.returncode != 0
    combined = (result.stdout + result.stderr).lower()
    assert "nope" in combined or "invalid" in combined or "scenario" in combined


# ------------------------------------------------------------------
# build
# ------------------------------------------------------------------


def test_build_creates_graph(tmp_path: Path) -> None:
    from contextweaver.routing.catalog import generate_sample_catalog

    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(generate_sample_catalog(n=20, seed=0)), encoding="utf-8")

    graph_path = tmp_path / "graph.json"
    result = _run("build", "--catalog", str(catalog_path), "--out", str(graph_path))
    assert result.returncode == 0
    assert graph_path.exists()
    assert "items" in result.stdout.lower() or "graph" in result.stdout.lower()


def test_build_custom_max_children(tmp_path: Path) -> None:
    from contextweaver.routing.catalog import generate_sample_catalog

    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(generate_sample_catalog(n=20, seed=1)), encoding="utf-8")

    graph_path = tmp_path / "graph.json"
    result = _run(
        "build", "--catalog", str(catalog_path), "--out", str(graph_path), "--max-children", "5"
    )
    assert result.returncode == 0
    assert graph_path.exists()


# ------------------------------------------------------------------
# route
# ------------------------------------------------------------------


def _make_graph(tmp_path: Path) -> tuple[Path, Path]:
    """Create a graph JSON file and catalog JSON file for route/print-tree tests."""
    from contextweaver.routing.catalog import generate_sample_catalog, load_catalog_dicts
    from contextweaver.routing.graph_io import save_graph
    from contextweaver.routing.tree import TreeBuilder

    dicts = generate_sample_catalog(n=20, seed=42)
    items = load_catalog_dicts(dicts)
    graph = TreeBuilder(max_children=10).build(items)
    graph_path = tmp_path / "graph.json"
    save_graph(graph, str(graph_path))
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(dicts), encoding="utf-8")
    return graph_path, catalog_path


def test_route_returns_results(tmp_path: Path) -> None:
    graph_path, catalog_path = _make_graph(tmp_path)
    result = _run(
        "route",
        "--graph",
        str(graph_path),
        "--catalog",
        str(catalog_path),
        "--query",
        "send an email",
    )
    assert result.returncode == 0
    assert "query" in result.stdout.lower() or "result" in result.stdout.lower()


def test_route_top_k(tmp_path: Path) -> None:
    graph_path, catalog_path = _make_graph(tmp_path)
    result = _run(
        "route",
        "--graph",
        str(graph_path),
        "--catalog",
        str(catalog_path),
        "--query",
        "database",
        "--top-k",
        "3",
    )
    assert result.returncode == 0


# ------------------------------------------------------------------
# print-tree
# ------------------------------------------------------------------


def test_print_tree_shows_tree(tmp_path: Path) -> None:
    graph_path, _ = _make_graph(tmp_path)
    result = _run("print-tree", "--graph", str(graph_path))
    assert result.returncode == 0
    assert (
        "tree" in result.stdout.lower()
        or "node" in result.stdout.lower()
        or "stats" in result.stdout.lower()
    )


def test_print_tree_depth_limit(tmp_path: Path) -> None:
    graph_path, _ = _make_graph(tmp_path)
    result = _run("print-tree", "--graph", str(graph_path), "--depth", "1")
    assert result.returncode == 0


# ------------------------------------------------------------------
# init
# ------------------------------------------------------------------


def test_init_creates_files(tmp_path: Path) -> None:
    result = _run("init", cwd=str(tmp_path))
    assert result.returncode == 0
    assert (tmp_path / "contextweaver.json").exists()
    assert (tmp_path / "sample_catalog.json").exists()
    # Validate JSON
    config = json.loads((tmp_path / "contextweaver.json").read_text(encoding="utf-8"))
    assert "version" in config
    catalog = json.loads((tmp_path / "sample_catalog.json").read_text(encoding="utf-8"))
    assert len(catalog) > 0


# ------------------------------------------------------------------
# ingest
# ------------------------------------------------------------------


def _write_session_jsonl(tmp_path: Path) -> Path:
    """Write a small JSONL session file."""
    lines = [
        {"id": "u1", "type": "user_turn", "text": "What is the status?"},
        {"id": "a1", "type": "agent_msg", "text": "Checking now."},
        {"id": "tc1", "type": "tool_call", "text": "get_status()", "parent_id": "u1"},
        {"id": "tr1", "type": "tool_result", "text": "status: OK", "parent_id": "tc1"},
    ]
    p = tmp_path / "session.jsonl"
    p.write_text("\n".join(json.dumps(row) for row in lines), encoding="utf-8")
    return p


def test_ingest_creates_session_file(tmp_path: Path) -> None:
    jsonl_path = _write_session_jsonl(tmp_path)
    out_path = tmp_path / "session_out.json"
    result = _run("ingest", "--events", str(jsonl_path), "--out", str(out_path))
    assert result.returncode == 0
    assert out_path.exists()
    session = json.loads(out_path.read_text(encoding="utf-8"))
    assert session["event_count"] == 4
    assert "ingested" in result.stdout.lower() or "events" in result.stdout.lower()


def test_ingest_firewall_trigger(tmp_path: Path) -> None:
    """Ensure tool results >2000 chars trigger the firewall."""
    big_text = "x" * 2500
    lines = [
        {"id": "u1", "type": "user_turn", "text": "Run query"},
        {"id": "tc1", "type": "tool_call", "text": "big_query()", "parent_id": "u1"},
        {"id": "tr1", "type": "tool_result", "text": big_text, "parent_id": "tc1"},
    ]
    p = tmp_path / "big_session.jsonl"
    p.write_text("\n".join(json.dumps(row) for row in lines), encoding="utf-8")
    out_path = tmp_path / "big_out.json"
    result = _run("ingest", "--events", str(p), "--out", str(out_path))
    assert result.returncode == 0
    assert "firewall" in result.stdout.lower()


# ------------------------------------------------------------------
# replay
# ------------------------------------------------------------------


def test_replay_preview(tmp_path: Path) -> None:
    """Replay requires an ingested session JSON (not raw JSONL)."""
    jsonl_path = _write_session_jsonl(tmp_path)
    ingested_path = tmp_path / "ingested.json"
    _run("ingest", "--events", str(jsonl_path), "--out", str(ingested_path))

    result = _run("replay", "--session", str(ingested_path), "--phase", "answer")
    assert result.returncode == 0
    assert "context build" in result.stdout.lower() or "prompt" in result.stdout.lower()


def test_replay_full(tmp_path: Path) -> None:
    jsonl_path = _write_session_jsonl(tmp_path)
    ingested_path = tmp_path / "ingested.json"
    _run("ingest", "--events", str(jsonl_path), "--out", str(ingested_path))

    result = _run("replay", "--session", str(ingested_path), "--full")
    assert result.returncode == 0


def test_replay_budget(tmp_path: Path) -> None:
    jsonl_path = _write_session_jsonl(tmp_path)
    ingested_path = tmp_path / "ingested.json"
    _run("ingest", "--events", str(jsonl_path), "--out", str(ingested_path))

    result = _run("replay", "--session", str(ingested_path), "--budget", "500")
    assert result.returncode == 0
    assert "500" in result.stdout


# ------------------------------------------------------------------
# stats (issue #106)
# ------------------------------------------------------------------


def test_stats_subcommand_renders_report(tmp_path: Path) -> None:
    """End-to-end: ingest a JSONL session, then run ``stats`` against it."""
    jsonl_path = _write_session_jsonl(tmp_path)
    ingested_path = tmp_path / "ingested.json"
    _run("ingest", "--events", str(jsonl_path), "--out", str(ingested_path))

    result = _run(
        "stats",
        "--session",
        str(ingested_path),
        "--phase",
        "answer",
        "--budget",
        "1000",
        "--format",
        "text",
    )
    assert result.returncode == 0
    assert "Context Build Report" in result.stdout
    assert "answer" in result.stdout
    assert "Candidates" in result.stdout


def test_stats_subcommand_rich_format(tmp_path: Path) -> None:
    jsonl_path = _write_session_jsonl(tmp_path)
    ingested_path = tmp_path / "ingested.json"
    _run("ingest", "--events", str(jsonl_path), "--out", str(ingested_path))

    result = _run("stats", "--session", str(ingested_path), "--budget", "1000", "--format", "rich")
    assert result.returncode == 0
    # Rich-rendered output strips markup but keeps the headers.
    assert "Context Build Report" in result.stdout
    assert "Candidates" in result.stdout


# ------------------------------------------------------------------
# inspect (issue #398)
# ------------------------------------------------------------------


def test_inspect_subcommand_emits_payload_safe_json(tmp_path: Path) -> None:
    jsonl_path = _write_session_jsonl(tmp_path)
    ingested_path = tmp_path / "ingested.json"
    _run("ingest", "--events", str(jsonl_path), "--out", str(ingested_path))

    result = _run(
        "inspect",
        "--session",
        str(ingested_path),
        "--budget",
        "1000",
        "--format",
        "json",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["version"] == 1
    assert payload["build"]["candidates"]["total"] > 0
    assert "prompt" not in payload
    assert "text" not in payload


def test_inspect_subcommand_markdown_lists_drop_reasons(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "events.jsonl"
    jsonl_path.write_text(
        '{"id":"big","type":"user_turn","text":"large","token_estimate":500}\n',
        encoding="utf-8",
    )
    ingested_path = tmp_path / "ingested.json"
    _run("ingest", "--events", str(jsonl_path), "--out", str(ingested_path))

    result = _run(
        "inspect",
        "--session",
        str(ingested_path),
        "--budget",
        "10",
    )

    assert result.returncode == 0, result.stderr
    assert "Context Inspection" in result.stdout
    assert "`big`: budget" in result.stdout


# ------------------------------------------------------------------
# budget-check (issue #276)
# ------------------------------------------------------------------


def test_budget_check_under_budget_passes(tmp_path: Path) -> None:
    jsonl_path = _write_session_jsonl(tmp_path)
    ingested_path = tmp_path / "ingested.json"
    _run("ingest", "--events", str(jsonl_path), "--out", str(ingested_path))

    result = _run(
        "budget-check",
        "--session",
        str(ingested_path),
        "--max-tokens",
        "1000",
        "--query",
        "status",
    )
    assert result.returncode == 0
    assert "OK total=" in result.stdout
    assert "budget=1000" in result.stdout


def test_budget_check_over_budget_fails(tmp_path: Path) -> None:
    jsonl_path = _write_session_jsonl(tmp_path)
    ingested_path = tmp_path / "ingested.json"
    _run("ingest", "--events", str(jsonl_path), "--out", str(ingested_path))

    result = _run(
        "budget-check",
        "--session",
        str(ingested_path),
        "--max-tokens",
        "1",
        "--query",
        "status",
    )
    assert result.returncode == 1
    assert "FAIL total=" in result.stdout
    assert "over=" in result.stdout


def test_budget_check_missing_session_file_is_usage_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"

    result = _run("budget-check", "--session", str(missing), "--max-tokens", "1000")

    assert result.returncode == 2
    assert "session file not found" in result.stderr


def test_budget_check_breakdown_output(tmp_path: Path) -> None:
    jsonl_path = _write_session_jsonl(tmp_path)
    ingested_path = tmp_path / "ingested.json"
    _run("ingest", "--events", str(jsonl_path), "--out", str(ingested_path))

    result = _run(
        "budget-check",
        "--session",
        str(ingested_path),
        "--max-tokens",
        "1000",
        "--breakdown",
    )
    assert result.returncode == 0
    assert "Token breakdown:" in result.stdout


def test_budget_check_json_output(tmp_path: Path) -> None:
    jsonl_path = _write_session_jsonl(tmp_path)
    ingested_path = tmp_path / "ingested.json"
    _run("ingest", "--events", str(jsonl_path), "--out", str(ingested_path))

    result = _run(
        "budget-check",
        "--session",
        str(ingested_path),
        "--max-tokens",
        "1000",
        "--json",
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["prompt_tokens"] <= payload["max_tokens"]
    assert payload["tokens_per_section"]


def test_budget_check_ratchet_write_and_compare(tmp_path: Path) -> None:
    jsonl_path = _write_session_jsonl(tmp_path)
    ingested_path = tmp_path / "ingested.json"
    _run("ingest", "--events", str(jsonl_path), "--out", str(ingested_path))
    baseline_path = tmp_path / ".budget-baseline.json"

    first = _run(
        "budget-check",
        "--session",
        str(ingested_path),
        "--max-tokens",
        "1000",
        "--ratchet",
        "--ratchet-path",
        str(baseline_path),
    )
    assert first.returncode == 0
    assert baseline_path.exists()
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert baseline["prompt_tokens"] > 0

    baseline["prompt_tokens"] = 0
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")

    second = _run(
        "budget-check",
        "--session",
        str(ingested_path),
        "--max-tokens",
        "1000",
        "--ratchet",
        "--ratchet-path",
        str(baseline_path),
    )
    assert second.returncode == 1
    assert "Ratchet failed:" in second.stdout


def test_budget_check_ratchet_uses_default_baseline_path(tmp_path: Path) -> None:
    jsonl_path = _write_session_jsonl(tmp_path)
    ingested_path = tmp_path / "ingested.json"
    _run("ingest", "--events", str(jsonl_path), "--out", str(ingested_path))

    result = _run(
        "budget-check",
        "--session",
        str(ingested_path),
        "--max-tokens",
        "1000",
        "--ratchet",
        cwd=str(tmp_path),
    )

    assert result.returncode == 0
    assert (tmp_path / ".budget-baseline.json").exists()


# ------------------------------------------------------------------
# catalog lint (issue #538)
# ------------------------------------------------------------------

_CLEAN_CATALOG = [
    {
        "id": "billing.invoices.create",
        "kind": "tool",
        "name": "create_invoice",
        "description": "Create an invoice",
        "tags": ["billing", "create"],
        "namespace": "billing",
    },
    {
        "id": "billing.invoices.get",
        "kind": "tool",
        "name": "get_invoice",
        "description": "Get an invoice",
        "tags": ["billing", "read"],
        "namespace": "billing",
    },
]


def test_catalog_lint_clean_exits_zero(tmp_path: Path) -> None:
    path = tmp_path / "clean.json"
    path.write_text(json.dumps(_CLEAN_CATALOG), encoding="utf-8")
    result = _run("catalog", "lint", str(path))
    assert result.returncode == 0
    assert "OK" in result.stdout


def test_catalog_lint_findings_exit_one(tmp_path: Path) -> None:
    dirty = [
        {
            "id": "a",
            "kind": "tool",
            "name": "a",
            "description": "   ",
            "tags": ["X", "x"],
            "namespace": "ns",
            "depends_on": ["ghost"],
        }
    ]
    path = tmp_path / "dirty.json"
    path.write_text(json.dumps(dirty), encoding="utf-8")
    result = _run("catalog", "lint", str(path))
    assert result.returncode == 1
    assert "FAIL" in result.stdout


def test_catalog_lint_json_output(tmp_path: Path) -> None:
    dirty = [
        {
            "id": "a",
            "kind": "tool",
            "name": "a",
            "description": "d",
            "depends_on": ["ghost"],
        }
    ]
    path = tmp_path / "dirty.json"
    path.write_text(json.dumps(dirty), encoding="utf-8")
    result = _run("catalog", "lint", str(path), "--json")
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["references"]["findings"][0]["missing"] == "ghost"


def test_catalog_lint_clean_json_ok_true(tmp_path: Path) -> None:
    path = tmp_path / "clean.json"
    path.write_text(json.dumps(_CLEAN_CATALOG), encoding="utf-8")
    result = _run("catalog", "lint", str(path), "--json")
    assert result.returncode == 0
    assert json.loads(result.stdout)["ok"] is True


def test_catalog_lint_accepts_mcp_snapshot(tmp_path: Path) -> None:
    snapshot = {"tools": [{"name": "github.search", "description": "Search repos"}]}
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    result = _run("catalog", "lint", str(path), "--json")
    assert result.returncode in (0, 1)
    assert "ok" in json.loads(result.stdout)


def test_catalog_lint_load_error_exits_three(tmp_path: Path) -> None:
    result = _run("catalog", "lint", str(tmp_path / "missing.json"))
    assert result.returncode == 3
    assert "Error" in result.stderr


def test_catalog_lint_malformed_yaml_exits_three(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("id: [unclosed\n", encoding="utf-8")
    result = _run("catalog", "lint", str(path))
    assert result.returncode == 3
    assert "Error" in result.stderr


def test_catalog_lint_committed_sample_is_clean() -> None:
    """The committed sample catalog must lint clean (issue #538 acceptance)."""
    sample = Path("examples/sample_catalog.json")
    if not sample.exists():
        return
    result = _run("catalog", "lint", str(sample))
    assert result.returncode == 0, result.stdout + result.stderr


# ------------------------------------------------------------------
# verify (issue #657)
# ------------------------------------------------------------------


def test_verify_subcommand_passes() -> None:
    """Happy path: all five checks pass."""
    result = _run("verify")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "import" in out.lower()
    assert "manager" in out.lower()
    assert "build" in out.lower()
    assert "tokens" in out.lower()
    assert "routing" in out.lower()
    assert "pass" in out.lower()
    assert "all checks passed" in out.lower()


def test_verify_subcommand_json_mode() -> None:
    """--json emits machine-readable payload."""
    result = _run("verify", "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert len(payload["checks"]) == 5
    check_names = [c["name"] for c in payload["checks"]]
    assert "import" in check_names
    assert "manager" in check_names
    assert "build" in check_names
    assert "tokens" in check_names
    assert "routing" in check_names
    assert all(c["ok"] is True for c in payload["checks"])
    assert "next_step" in payload


def _failing_routing_check() -> _VerifyCheck:
    return _VerifyCheck(
        name="routing",
        ok=False,
        detail="simulated routing failure",
        fix_hint="File an issue with the full traceback",
    )


def test_verify_subcommand_failure_exits_nonzero_and_renders_fix_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing check yields exit 1 and prints its fix hint (issue #706).

    Driven in-process so a single check can be made to fail deterministically;
    ``_console`` is redirected to a buffer because Rich binds to the real
    stdout at import time and would otherwise escape ``CliRunner`` capture.
    """
    monkeypatch.setattr("contextweaver.__main__._check_routing", _failing_routing_check)
    buffer = io.StringIO()
    monkeypatch.setattr(
        "contextweaver.__main__._console",
        Console(file=buffer, force_terminal=False, width=200),
    )

    result = CliRunner().invoke(app, ["verify"])

    assert result.exit_code == 1
    rendered = buffer.getvalue()
    assert "FAIL" in rendered
    assert "routing" in rendered
    assert "File an issue with the full traceback" in rendered


def test_verify_subcommand_failure_json_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """--json reports ok=False and the failing check on the failure path (#706)."""
    monkeypatch.setattr("contextweaver.__main__._check_routing", _failing_routing_check)

    result = CliRunner().invoke(app, ["verify", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    routing = next(c for c in payload["checks"] if c["name"] == "routing")
    assert routing["ok"] is False
    assert routing["fix_hint"] == "File an issue with the full traceback"


# ------------------------------------------------------------------
# consolidate (issue #498)
# ------------------------------------------------------------------


def _write_episodes(path: Path) -> None:
    summary = "customer prefers email contact for support"
    episodes = [
        {"episode_id": f"ep{i}", "summary": summary, "metadata": {"session_id": f"s{i}"}}
        for i in range(3)
    ]
    path.write_text(json.dumps({"episodes": episodes}), encoding="utf-8")


def test_consolidate_subcommand_json(tmp_path: Path) -> None:
    eps = tmp_path / "episodes.json"
    _write_episodes(eps)
    result = _run(
        "consolidate",
        "--episodes",
        str(eps),
        "--min-occurrences",
        "3",
        "--min-sessions",
        "2",
        "--json",
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert len(report["promoted"]) == 1
    assert report["promoted"][0]["occurrences"] == 3
    assert report["applied"] is False


def test_consolidate_subcommand_apply_writes_facts(tmp_path: Path) -> None:
    eps = tmp_path / "episodes.json"
    out = tmp_path / "facts.json"
    _write_episodes(eps)
    result = _run(
        "consolidate",
        "--episodes",
        str(eps),
        "--apply",
        "--facts-out",
        str(out),
        "--min-occurrences",
        "3",
        "--min-sessions",
        "2",
    )
    assert result.returncode == 0, result.stderr
    assert "applied=True" in result.stdout
    written = json.loads(out.read_text(encoding="utf-8"))
    assert len(written["facts"]) == 1
    assert written["facts"][0]["key"] == "consolidated"


def test_consolidate_subcommand_bad_file(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    result = _run("consolidate", "--episodes", str(missing))
    assert result.returncode != 0


def test_consolidate_subcommand_reports_decay_by_default(tmp_path: Path) -> None:
    eps = tmp_path / "episodes.json"
    episodes = [
        {
            "episode_id": f"ep{i}",
            "summary": "customer prefers email contact for support",
            "metadata": {"session_id": f"s{i}", "timestamp": "2020-01-01T00:00:00Z"},
        }
        for i in range(3)
    ]
    eps.write_text(json.dumps({"episodes": episodes}), encoding="utf-8")
    # No --as-of: defaults to now, so the 2020 timestamps decay.
    result = _run("consolidate", "--episodes", str(eps), "--json")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert len(report["decayed_episode_ids"]) == 3


def test_consolidate_subcommand_accepts_z_as_of(tmp_path: Path) -> None:
    eps = tmp_path / "episodes.json"
    _write_episodes(eps)
    result = _run(
        "consolidate", "--episodes", str(eps), "--as-of", "2026-06-01T00:00:00Z", "--json"
    )
    assert result.returncode == 0, result.stderr
