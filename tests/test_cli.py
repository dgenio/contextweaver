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


def test_build_subcommand() -> None:
    result = _run("build", "session.jsonl")
    assert result.returncode == 0


def test_route_subcommand() -> None:
    result = _run("route", "find data", "catalog.json")
    assert result.returncode == 0


def test_print_tree_subcommand() -> None:
    result = _run("print-tree", "catalog.json")
    assert result.returncode == 0


def test_init_subcommand() -> None:
    result = _run("init", "/tmp/test_init_dir")
    assert result.returncode == 0


def test_ingest_subcommand() -> None:
    result = _run("ingest", "file.txt")
    assert result.returncode == 0


def test_replay_subcommand() -> None:
    result = _run("replay", "session.jsonl")
    assert result.returncode == 0
