"""Tests for contextweaver._vscode_import (issue #367)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contextweaver._vscode_import import (
    build_migration_plan,
    load_vscode_mcp_config,
    render_dry_run_report,
    render_gateway_config,
    render_replacement_config,
    write_migration,
)
from contextweaver.exceptions import ConfigError

_SIMPLE_CONFIG = {
    "servers": {
        "filesystem": {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"],
        },
        "github": {
            "url": "https://example.com/mcp",
            "headers": {"Authorization": "Bearer ${env:TOKEN}"},
        },
    }
}


def test_load_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_vscode_mcp_config(tmp_path / "missing.json")


def test_load_malformed_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    path.write_text("{not json")
    with pytest.raises(ConfigError, match="invalid"):
        load_vscode_mcp_config(path)


def test_build_plan_converts_stdio_and_url_servers() -> None:
    plan = build_migration_plan(_SIMPLE_CONFIG)
    assert plan.server_map_key == "servers"
    assert set(plan.upstreams) == {"filesystem", "github"}
    assert plan.upstreams["filesystem"]["type"] == "stdio"
    assert plan.upstreams["filesystem"]["command"] == "npx"
    assert plan.upstreams["filesystem"]["namespace"] == "filesystem"
    assert plan.upstreams["github"]["type"] == "http"
    assert plan.upstreams["github"]["url"] == "https://example.com/mcp"
    assert not plan.skipped
    assert not plan.warnings


def test_build_plan_accepts_mcp_servers_key() -> None:
    plan = build_migration_plan({"mcpServers": {"a": {"command": "echo"}}})
    assert plan.server_map_key == "mcpServers"
    assert "a" in plan.upstreams


def test_build_plan_skips_unsupported_shape_with_warning() -> None:
    plan = build_migration_plan(
        {"servers": {"good": {"command": "echo"}, "bad": {"unknown": "shape"}}}
    )
    assert "good" in plan.upstreams
    assert plan.skipped == ["bad"]
    assert any("unsupported shape" in w for w in plan.warnings)


def test_build_plan_no_recognised_servers_key_raises() -> None:
    with pytest.raises(ConfigError, match="declares no servers"):
        build_migration_plan({"unrelated": {}})


def test_render_gateway_config_is_upstreams_only() -> None:
    plan = build_migration_plan(_SIMPLE_CONFIG)
    gateway_config = render_gateway_config(plan)
    assert set(gateway_config) == {"upstreams"}


def test_render_replacement_config_embeds_config_arg() -> None:
    plan = build_migration_plan(_SIMPLE_CONFIG)
    replacement = render_replacement_config(plan, gateway_config_arg="${workspaceFolder}/gw.json")
    server = replacement["servers"]["contextweaver-gateway"]
    assert server["args"][-1] == "${workspaceFolder}/gw.json"
    assert "--config" in server["args"]


def test_dry_run_report_lists_servers_and_skips(tmp_path: Path) -> None:
    plan = build_migration_plan(
        {"servers": {"good": {"command": "echo"}, "bad": {"unknown": "shape"}}}
    )
    report = render_dry_run_report(
        plan, gateway_config_path=tmp_path / "gw.json", output_path=tmp_path / "mcp.json"
    )
    assert "good" in report
    assert "skipped 1 server" in report
    assert "dry run" in report


def test_write_migration_creates_files_and_backs_up(tmp_path: Path) -> None:
    output_path = tmp_path / "mcp.json"
    output_path.write_text(json.dumps(_SIMPLE_CONFIG))
    gateway_path = tmp_path / ".contextweaver" / "gateway.json"

    plan = build_migration_plan(_SIMPLE_CONFIG)
    written = write_migration(
        plan,
        gateway_config_path=gateway_path,
        gateway_config_arg="${workspaceFolder}/.contextweaver/gateway.json",
        output_path=output_path,
        backup=True,
    )

    assert gateway_path in written
    backup_path = output_path.with_suffix(output_path.suffix + ".bak")
    assert backup_path in written
    assert backup_path.exists()
    assert json.loads(backup_path.read_text()) == _SIMPLE_CONFIG

    gateway_data = json.loads(gateway_path.read_text())
    assert "filesystem" in gateway_data["upstreams"]

    replacement_data = json.loads(output_path.read_text())
    assert list(replacement_data["servers"]) == ["contextweaver-gateway"]


def test_write_migration_no_backup_when_output_absent(tmp_path: Path) -> None:
    plan = build_migration_plan(_SIMPLE_CONFIG)
    written = write_migration(
        plan,
        gateway_config_path=tmp_path / "gw.json",
        gateway_config_arg="gw.json",
        output_path=tmp_path / "mcp.json",
        backup=True,
    )
    assert not any(str(p).endswith(".bak") for p in written)
    assert written == [tmp_path / "gw.json", tmp_path / "mcp.json"]
