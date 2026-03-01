"""Tests for the contextweaver CLI (__main__.py) -- all 7 CLI commands exit 0, check output fragments."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from contextweaver.routing.catalog import generate_sample_catalog


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "contextweaver", *args],
        capture_output=True,
        text=True,
    )


def test_no_args_exits_zero() -> None:
    result = _run()
    assert result.returncode == 0


def test_demo_subcommand() -> None:
    result = _run("demo")
    assert result.returncode == 0
    assert "demo" in result.stdout.lower()


def test_build_subcommand() -> None:
    """build requires --catalog pointing to a valid JSON file."""
    catalog_data = generate_sample_catalog(n=10, seed=42)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(catalog_data, f)
        catalog_path = f.name
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as out:
        out_path = out.name

    result = _run("build", "--catalog", catalog_path, "--out", out_path)
    Path(catalog_path).unlink(missing_ok=True)
    assert result.returncode == 0
    assert "Loaded" in result.stdout
    # Check that the output file was created
    assert Path(out_path).exists()
    Path(out_path).unlink(missing_ok=True)


def test_route_subcommand() -> None:
    """route requires --graph and --query."""
    # First build a graph
    catalog_data = generate_sample_catalog(n=10, seed=42)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(catalog_data, f)
        catalog_path = f.name
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as out:
        graph_path = out.name

    _run("build", "--catalog", catalog_path, "--out", graph_path)
    result = _run("route", "--graph", graph_path, "--query", "search invoices")
    Path(catalog_path).unlink(missing_ok=True)
    Path(graph_path).unlink(missing_ok=True)
    assert result.returncode == 0
    assert "Query" in result.stdout


def test_print_tree_subcommand() -> None:
    """print-tree requires --graph."""
    catalog_data = generate_sample_catalog(n=10, seed=42)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(catalog_data, f)
        catalog_path = f.name
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as out:
        graph_path = out.name

    _run("build", "--catalog", catalog_path, "--out", graph_path)
    result = _run("print-tree", "--graph", graph_path)
    Path(catalog_path).unlink(missing_ok=True)
    Path(graph_path).unlink(missing_ok=True)
    assert result.returncode == 0
    assert "Graph" in result.stdout


def test_init_subcommand() -> None:
    result = _run("init")
    assert result.returncode == 0
    assert "Created" in result.stdout
    # Clean up created files
    Path("sample_catalog.json").unlink(missing_ok=True)
    Path("contextweaver_config.json").unlink(missing_ok=True)


def test_ingest_subcommand() -> None:
    """ingest requires --events pointing to a valid JSONL file."""
    events = [
        {"type": "user_turn", "id": "u1", "text": "Hello", "timestamp": 1.0},
        {"type": "agent_msg", "id": "a1", "text": "Hi there", "timestamp": 2.0},
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        for event in events:
            f.write(json.dumps(event) + "\n")
        events_path = f.name
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as out:
        out_path = out.name

    result = _run("ingest", "--events", events_path, "--out", out_path)
    Path(events_path).unlink(missing_ok=True)
    assert result.returncode == 0
    assert "Loaded" in result.stdout
    Path(out_path).unlink(missing_ok=True)


def test_replay_subcommand() -> None:
    """replay requires --session pointing to a valid session JSON file."""
    session = {
        "items": [
            {
                "id": "u1",
                "kind": "user_turn",
                "text": "Hello",
                "token_estimate": 2,
                "metadata": {},
            },
        ],
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(session, f)
        session_path = f.name

    result = _run("replay", "--session", session_path)
    Path(session_path).unlink(missing_ok=True)
    assert result.returncode == 0
    assert "Replay results" in result.stdout
