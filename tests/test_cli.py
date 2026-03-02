"""Tests for the contextweaver CLI (__main__.py).

Each test exercises a real subcommand via subprocess, creating any needed
temp files on the fly.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _run(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "contextweaver", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


# ------------------------------------------------------------------
# No-args / help
# ------------------------------------------------------------------


def test_no_args_prints_help() -> None:
    result = _run()
    assert result.returncode == 0
    assert "contextweaver" in result.stdout.lower() or "usage" in result.stdout.lower()


# ------------------------------------------------------------------
# demo
# ------------------------------------------------------------------


def test_demo_runs_to_completion() -> None:
    result = _run("demo")
    assert result.returncode == 0
    assert "demo" in result.stdout.lower()
    assert "complete" in result.stdout.lower() or "loaded" in result.stdout.lower()


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


def _make_graph(tmp_path: Path) -> Path:
    """Create a graph JSON file for route/print-tree tests."""
    from contextweaver.routing.catalog import generate_sample_catalog, load_catalog_dicts
    from contextweaver.routing.graph_io import save_graph
    from contextweaver.routing.tree import TreeBuilder

    dicts = generate_sample_catalog(n=20, seed=42)
    items = load_catalog_dicts(dicts)
    graph = TreeBuilder(max_children=10).build(items)
    graph_path = tmp_path / "graph.json"
    save_graph(graph, str(graph_path))
    return graph_path


def test_route_returns_results(tmp_path: Path) -> None:
    graph_path = _make_graph(tmp_path)
    result = _run("route", "--graph", str(graph_path), "--query", "send an email")
    assert result.returncode == 0
    assert "query" in result.stdout.lower() or "result" in result.stdout.lower()


def test_route_top_k(tmp_path: Path) -> None:
    graph_path = _make_graph(tmp_path)
    result = _run("route", "--graph", str(graph_path), "--query", "database", "--top-k", "3")
    assert result.returncode == 0


# ------------------------------------------------------------------
# print-tree
# ------------------------------------------------------------------


def test_print_tree_shows_tree(tmp_path: Path) -> None:
    graph_path = _make_graph(tmp_path)
    result = _run("print-tree", "--graph", str(graph_path))
    assert result.returncode == 0
    assert (
        "tree" in result.stdout.lower()
        or "node" in result.stdout.lower()
        or "stats" in result.stdout.lower()
    )


def test_print_tree_depth_limit(tmp_path: Path) -> None:
    graph_path = _make_graph(tmp_path)
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
