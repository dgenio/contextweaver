"""Tests for the contextweaver CLI (__main__.py)."""

from __future__ import annotations

import subprocess
import sys


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


def test_build_subcommand_requires_catalog() -> None:
    result = _run("build", "--catalog", "nonexistent.json")
    assert result.returncode == 1


def test_route_subcommand_requires_graph() -> None:
    result = _run("route", "--graph", "nonexistent.json", "--query", "find data")
    assert result.returncode == 1


def test_print_tree_subcommand_requires_graph() -> None:
    result = _run("print-tree", "--graph", "nonexistent.json")
    assert result.returncode == 1


def test_init_subcommand() -> None:
    result = _run("init")
    assert result.returncode == 0


def test_ingest_subcommand_requires_events() -> None:
    result = _run("ingest", "--events", "nonexistent.jsonl")
    assert result.returncode == 1


def test_replay_subcommand_requires_session() -> None:
    result = _run("replay", "--session", "nonexistent.json")
    assert result.returncode == 1
