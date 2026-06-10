"""Tests for the MCP client configs shipped under ``examples/recipes/``.

These files are *examples* — they are not executed at runtime by the
library — but the client recipes embed them verbatim. A regression would
surface as a copy-paste failure for downstream users hours after the bad
commit lands. These tests pin the structural invariants so that does not
happen.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_RECIPES_DIR = _REPO_ROOT / "examples" / "recipes"
_UVX_PREFIX = ["contextweaver", "mcp", "serve"]


def _load_json(path: Path) -> dict[str, object]:
    """Return the parsed JSON object at *path*."""
    payload = json.loads(path.read_text())
    assert isinstance(payload, dict), f"{path.name} top-level must be a JSON object"
    return payload


def test_claude_desktop_config_is_valid_json() -> None:
    """The Claude Desktop config is valid JSON with the expected top-level keys."""
    payload = _load_json(_RECIPES_DIR / "claude_desktop_config.json")
    assert "mcpServers" in payload, "Claude Desktop config must use 'mcpServers' key"
    servers = payload["mcpServers"]
    assert isinstance(servers, dict)
    assert "contextweaver-gateway" in servers


def _assert_uvx_gateway(server: object) -> list[object]:
    """Assert *server* launches the installed CLI through uvx."""
    assert isinstance(server, dict)
    assert server["command"] == "uvx"
    args = server["args"]
    assert isinstance(args, list)
    assert args[: len(_UVX_PREFIX)] == _UVX_PREFIX
    legacy_args = [
        arg
        for arg in args
        if str(arg).replace("\\", "/").rsplit("/", maxsplit=1)[-1] == "serve_gateway.py"
    ]
    assert not legacy_args, f"Recipe config must not use the legacy launcher: {legacy_args}"
    return args


@pytest.mark.parametrize(
    "legacy_arg",
    [
        "/opt/contextweaver/examples/recipes/serve_gateway.py",
        r"C:\contextweaver\examples\recipes\serve_gateway.py",
    ],
)
def test_uvx_gateway_rejects_absolute_legacy_launcher_paths(legacy_arg: str) -> None:
    """The legacy-launcher invariant handles POSIX and Windows absolute paths."""
    server = {"command": "uvx", "args": [*_UVX_PREFIX, legacy_arg]}
    with pytest.raises(AssertionError, match="legacy launcher"):
        _assert_uvx_gateway(server)


def test_claude_desktop_config_invokes_installed_cli() -> None:
    """The Claude Desktop config invokes the packaged CLI with absolute paths."""
    payload = _load_json(_RECIPES_DIR / "claude_desktop_config.json")
    server = payload["mcpServers"]["contextweaver-gateway"]  # type: ignore[index]
    args = _assert_uvx_gateway(server)
    assert "--config" in args
    assert "--catalog" in args
    assert any("/ABSOLUTE/PATH/TO/contextweaver/" in str(arg) for arg in args)


def test_copilot_mcp_config_is_valid_json() -> None:
    """The Copilot mcp.json is valid JSON with VS Code's 'servers' shape."""
    payload = _load_json(_RECIPES_DIR / "copilot_mcp.json")
    assert "servers" in payload, "VS Code MCP config must use 'servers' key"
    servers = payload["servers"]
    assert isinstance(servers, dict)
    assert "contextweaver-gateway" in servers
    # VS Code's MCP schema demands an explicit transport type for stdio entries.
    server = servers["contextweaver-gateway"]
    assert isinstance(server, dict)
    assert server.get("type") == "stdio"


def test_copilot_mcp_config_uses_workspace_variable() -> None:
    """The Copilot config uses ${workspaceFolder} so the file is portable across clones."""
    payload = _load_json(_RECIPES_DIR / "copilot_mcp.json")
    server = payload["servers"]["contextweaver-gateway"]  # type: ignore[index]
    args = _assert_uvx_gateway(server)
    assert any("${workspaceFolder}" in str(arg) for arg in args), (
        "VS Code config must use ${workspaceFolder} for portability across clones"
    )
    assert "--config" in args


def test_claude_code_config_uses_project_root_expansion() -> None:
    """Claude Code resolves the shared config from its documented project root."""
    payload = _load_json(_RECIPES_DIR / "claude_code_mcp.json")
    server = payload["mcpServers"]["contextweaver-gateway"]  # type: ignore[index]
    args = _assert_uvx_gateway(server)
    assert server["type"] == "stdio"  # type: ignore[index]
    assert any("${CLAUDE_PROJECT_DIR:-.}" in str(arg) for arg in args)


def test_cursor_config_invokes_installed_cli() -> None:
    """Cursor launches the same packaged CLI and config as the other clients."""
    payload = _load_json(_RECIPES_DIR / "cursor_mcp.json")
    server = payload["mcpServers"]["contextweaver-gateway"]  # type: ignore[index]
    args = _assert_uvx_gateway(server)
    assert any("${workspaceFolder}" in str(arg) for arg in args)


@pytest.mark.parametrize(
    "filename",
    [
        "claude_code_mcp.json",
        "claude_desktop_config.json",
        "copilot_mcp.json",
        "cursor_mcp.json",
    ],
)
def test_recipe_configs_exist(filename: str) -> None:
    """Every documented client config must ship in the repository."""
    assert (_RECIPES_DIR / filename).is_file(), (
        f"Recipe config {filename} is missing; the recipes docs link to it directly"
    )
