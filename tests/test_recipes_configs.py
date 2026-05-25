"""Tests for the recipe config files shipped under ``examples/recipes/`` (#278, #279).

These files are *examples* — they are not executed at runtime by the
library — but the recipes (`docs/recipes/claude_desktop.md`,
`docs/recipes/github_copilot.md`) embed them verbatim. A regression in
either file would surface as a copy-paste failure for downstream users
hours after the bad commit lands. These tests pin the structural
invariants so that does not happen.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_RECIPES_DIR = _REPO_ROOT / "examples" / "recipes"
_LAUNCHER_PATH = "examples/recipes/serve_gateway.py"


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


def test_claude_desktop_config_references_launcher() -> None:
    """The Claude Desktop config invokes the recipe launcher with --catalog."""
    payload = _load_json(_RECIPES_DIR / "claude_desktop_config.json")
    server = payload["mcpServers"]["contextweaver-gateway"]  # type: ignore[index]
    assert isinstance(server, dict)
    assert server["command"] == "python"
    args = server["args"]
    assert isinstance(args, list)
    assert any(_LAUNCHER_PATH in str(arg) for arg in args), (
        "Claude Desktop config must reference examples/recipes/serve_gateway.py"
    )
    assert "--catalog" in args, "Claude Desktop config must pass --catalog"


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
    args = server["args"]  # type: ignore[index]
    assert isinstance(args, list)
    assert any("${workspaceFolder}" in str(arg) for arg in args), (
        "VS Code config must use ${workspaceFolder} for portability across clones"
    )
    assert any(_LAUNCHER_PATH in str(arg) for arg in args)


@pytest.mark.parametrize(
    "filename",
    ["claude_desktop_config.json", "copilot_mcp.json"],
)
def test_recipe_configs_exist(filename: str) -> None:
    """Both recipe config files must ship in the repo (docs link to them)."""
    assert (_RECIPES_DIR / filename).is_file(), (
        f"Recipe config {filename} is missing; the recipes docs link to it directly"
    )
