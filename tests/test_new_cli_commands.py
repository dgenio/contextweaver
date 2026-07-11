"""In-process smoke tests for the CLI commands added in the platform-maturity PR.

Covers the thin Typer wrappers for the new gateway/ops/governance surfaces:
``mcp doctor`` / ``mcp scorecard`` / ``mcp ops`` / ``mcp status`` (#395/#380/
#668/#655), ``catalog diff`` (#514), and ``models doctor`` (#386).  The
underlying library modules have their own exhaustive suites; these assert the
wiring — exit codes, output routing, and error handling.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from contextweaver.__main__ import app

_RUNNER = CliRunner()


def _catalog(path: Path) -> Path:
    items = [
        {
            "id": "fs::read",
            "kind": "tool",
            "name": "read_file",
            "description": "Read a file from disk by path.",
            "namespace": "fs",
            "args_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
        {
            "id": "fs::write",
            "kind": "tool",
            "name": "write_file",
            "description": "Write bytes to a file on disk.",
            "namespace": "fs",
            "args_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
    ]
    path.write_text(json.dumps(items), encoding="utf-8")
    return path


def _diagnostics(path: Path) -> Path:
    events = [
        {
            "version": 1,
            "event": "browse.completed",
            "timestamp": "2026-07-10T12:00:00+00:00",
            "success": True,
            "duration_ms": 12.0,
            "session_id": "s1",
            "tool_id": None,
            "namespace": None,
            "attributes": {"tool_ids": ["fs::read"]},
        },
        {
            "version": 1,
            "event": "execute.completed",
            "timestamp": "2026-07-10T12:00:01+00:00",
            "success": True,
            "duration_ms": 40.0,
            "session_id": "s1",
            "tool_id": "fs::read",
            "namespace": "fs",
            "attributes": {},
        },
    ]
    path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return path


def test_mcp_doctor_ok_config_exits_zero(tmp_path: Path) -> None:
    config = tmp_path / "gw.yaml"
    config.write_text(
        yaml.safe_dump({"catalog": str(_catalog(tmp_path / "cat.json"))}), encoding="utf-8"
    )
    result = _RUNNER.invoke(app, ["mcp", "doctor", "--config", str(config)])
    assert result.exit_code == 0, result.output
    assert "config.parse" in result.output


def test_mcp_doctor_json_and_bad_config(tmp_path: Path) -> None:
    config = tmp_path / "bad.yaml"
    config.write_text("catalog: /nope.json\nupstreams: {}\n", encoding="utf-8")  # both → fail
    result = _RUNNER.invoke(app, ["mcp", "doctor", "--config", str(config), "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["counts"]["fail"] >= 1


def test_mcp_scorecard_renders(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path / "cat.json")
    diagnostics = _diagnostics(tmp_path / "diag.jsonl")
    result = _RUNNER.invoke(
        app, ["mcp", "scorecard", "--catalog", str(catalog), "--diagnostics", str(diagnostics)]
    )
    assert result.exit_code == 0, result.output
    assert "Scorecard" in result.output or "scorecard" in result.output.lower()


def test_mcp_scorecard_bad_format(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path / "cat.json")
    diagnostics = _diagnostics(tmp_path / "diag.jsonl")
    result = _RUNNER.invoke(
        app,
        [
            "mcp",
            "scorecard",
            "--catalog",
            str(catalog),
            "--diagnostics",
            str(diagnostics),
            "--format",
            "xml",
        ],
    )
    assert result.exit_code != 0


def test_mcp_ops_snapshot(tmp_path: Path) -> None:
    diagnostics = _diagnostics(tmp_path / "diag.jsonl")
    result = _RUNNER.invoke(app, ["mcp", "ops", "--diagnostics", str(diagnostics)])
    assert result.exit_code == 0, result.output
    assert "events:" in result.output


def test_mcp_status_missing_file_errors(tmp_path: Path) -> None:
    result = _RUNNER.invoke(app, ["mcp", "status", "--state-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "status" in result.output.lower()


def test_catalog_diff_identical(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path / "cat.json")
    result = _RUNNER.invoke(app, ["catalog", "diff", str(catalog), str(catalog), "--probes", "2"])
    assert result.exit_code == 0, result.output
    assert "Added: 0" in result.output or "added" in result.output.lower()


def test_catalog_diff_json_detects_change(tmp_path: Path) -> None:
    before = _catalog(tmp_path / "before.json")
    after = tmp_path / "after.json"
    items = json.loads(before.read_text())
    items.pop()  # drop fs::write
    after.write_text(json.dumps(items), encoding="utf-8")
    result = _RUNNER.invoke(
        app, ["catalog", "diff", str(before), str(after), "--probes", "0", "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["diff"]["removed"] == ["fs::write"]


def test_models_doctor_runs(tmp_path: Path) -> None:
    result = _RUNNER.invoke(app, ["models", "doctor", "--provider", "hashing", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["ok"] is True


def test_visualize_requires_exactly_one_source(tmp_path: Path) -> None:
    result = _RUNNER.invoke(app, ["visualize", "--output", str(tmp_path / "r.html")])
    assert result.exit_code != 0
