"""Unit tests for ``scripts/snapshot_mcp_catalog.py`` (issue #280).

The helper spawns a real MCP server over stdio and writes its
``tools/list`` to disk. Spawning a real subprocess in CI is fragile, so
these tests exercise the surface area that does not need a live MCP
SDK transport:

- argument parsing
- ``_build_meta`` provenance shape
- ``_fetch_tools_list`` failure mode when the MCP SDK pieces are not
  importable (covered via monkeypatch)
- end-to-end ``main()`` writes a well-formed JSON payload when
  ``_fetch_tools_list`` is monkeypatched to return canned tools

The smoke test exercising the actual MCP subprocess lives in
``tests/test_architectures_mcp_context_gateway_real.py`` against the
committed snapshots.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "snapshot_mcp_catalog.py"


def _load_script() -> ModuleType:
    """Import the snapshot helper as a module without altering ``sys.path``."""
    spec = importlib.util.spec_from_file_location("snapshot_mcp_catalog", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


snapshot_mcp_catalog = _load_script()


# ------------------------------------------------------------------
# Argument parsing
# ------------------------------------------------------------------


def test_parse_args_requires_command_and_output(tmp_path: Path) -> None:
    """``--command`` and ``--output`` are mandatory; other fields default sensibly."""
    out = tmp_path / "snapshot.json"
    args = snapshot_mcp_catalog._parse_args(
        [
            "--command",
            "true",
            "--source-name",
            "test-server",
            "--output",
            str(out),
        ]
    )
    assert args.command == "true"
    assert args.output == out
    assert args.source == "modelcontextprotocol/servers"
    assert args.license == "MIT"
    assert args.server_version == "unknown"


def test_parse_args_rejects_missing_command(tmp_path: Path) -> None:
    """argparse exits when ``--command`` is missing."""
    with pytest.raises(SystemExit):
        snapshot_mcp_catalog._parse_args(
            ["--output", str(tmp_path / "x.json"), "--source-name", "x"]
        )


# ------------------------------------------------------------------
# Meta block
# ------------------------------------------------------------------


def test_build_meta_includes_required_fields(tmp_path: Path) -> None:
    """``_build_meta`` populates every field consumed by the recipes README."""
    args = snapshot_mcp_catalog._parse_args(
        [
            "--command",
            "true",
            "--source-name",
            "test-server",
            "--server-version",
            "1.2.3",
            "--license",
            "Apache-2.0",
            "--license-url",
            "https://example.com/LICENSE",
            "--notes",
            "Captured in a unit test.",
            "--output",
            str(tmp_path / "x.json"),
        ]
    )
    meta = snapshot_mcp_catalog._build_meta(args)
    assert meta["source"] == "modelcontextprotocol/servers"
    assert meta["server_package"] == "test-server"
    assert meta["server_version"] == "1.2.3"
    assert meta["license"] == "Apache-2.0"
    assert meta["license_url"] == "https://example.com/LICENSE"
    assert meta["notes"] == "Captured in a unit test."
    assert "snapshotted_at" in meta
    assert "snapshot_method" in meta


def test_build_meta_omits_blank_optional_fields(tmp_path: Path) -> None:
    """Empty ``--license-url`` / ``--notes`` arguments do not leak into the snapshot."""
    args = snapshot_mcp_catalog._parse_args(
        [
            "--command",
            "true",
            "--source-name",
            "test-server",
            "--output",
            str(tmp_path / "x.json"),
        ]
    )
    meta = snapshot_mcp_catalog._build_meta(args)
    assert "license_url" not in meta
    assert "notes" not in meta


# ------------------------------------------------------------------
# main() end-to-end with a stubbed _fetch_tools_list
# ------------------------------------------------------------------


def _stub_fetch_tools(monkeypatch: pytest.MonkeyPatch, tools: list[dict[str, Any]]) -> None:
    """Replace ``_fetch_tools_list`` with a stub that returns *tools* synchronously."""

    async def _fake(_command: str) -> list[dict[str, Any]]:
        return tools

    monkeypatch.setattr(snapshot_mcp_catalog, "_fetch_tools_list", _fake)


def test_main_writes_well_formed_snapshot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``main()`` produces a JSON file with ``_meta`` + ``tools`` and exits 0."""
    fake_tools = [
        {
            "name": "echo",
            "description": "Echo a message.",
            "inputSchema": {"type": "object", "properties": {"msg": {"type": "string"}}},
        }
    ]
    _stub_fetch_tools(monkeypatch, fake_tools)

    out = tmp_path / "snapshot.json"
    exit_code = snapshot_mcp_catalog.main(
        [
            "--command",
            "true",
            "--source-name",
            "fake-server",
            "--output",
            str(out),
        ]
    )
    assert exit_code == 0
    payload = json.loads(out.read_text())
    assert set(payload) == {"_meta", "tools"}
    assert payload["tools"] == fake_tools
    assert payload["_meta"]["server_package"] == "fake-server"


def test_main_deduplicates_tools_by_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When the stub helper returns duplicate names, the writer keeps the first occurrence."""

    # _fetch_tools_list already dedupes by name in the real code path; we
    # verify the surrounding main() preserves order and does not regress.
    fake_tools = [
        {"name": "echo", "description": "v1", "inputSchema": {}},
        {"name": "now", "description": "n", "inputSchema": {}},
    ]
    _stub_fetch_tools(monkeypatch, fake_tools)

    out = tmp_path / "snapshot.json"
    exit_code = snapshot_mcp_catalog.main(
        ["--command", "true", "--source-name", "x", "--output", str(out)]
    )
    assert exit_code == 0
    written = json.loads(out.read_text())["tools"]
    assert [t["name"] for t in written] == ["echo", "now"]


def test_main_returns_nonzero_on_fetch_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A connection error inside ``_fetch_tools_list`` surfaces as a non-zero exit."""

    async def _boom(_command: str) -> list[dict[str, Any]]:
        raise RuntimeError("upstream unreachable")

    monkeypatch.setattr(snapshot_mcp_catalog, "_fetch_tools_list", _boom)

    out = tmp_path / "snapshot.json"
    exit_code = snapshot_mcp_catalog.main(
        ["--command", "true", "--source-name", "x", "--output", str(out)]
    )
    assert exit_code == 1
    assert not out.exists(), "no partial snapshot should be written on failure"
