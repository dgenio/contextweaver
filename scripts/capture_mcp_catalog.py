#!/usr/bin/env python3
"""Capture a real MCP server's tools/list response into a JSON snapshot (#280).

Stands up an MCP server as a subprocess over stdio, calls ``tools/list``,
and writes the response to a JSON file under
``examples/architectures/mcp_context_gateway/real_catalogs/`` (or the
``--output`` path).

The committed snapshots under ``real_catalogs/*.json`` were captured this
way and are version-controlled so the architecture stays deterministic
and CI doesn't need network access. Re-run this when an upstream server
adds, renames, or removes tools.

Offline-safe: if the upstream cannot be reached or the SDK is missing,
the script exits with a non-zero status and **leaves any existing
snapshot untouched**. Output is only written when the call succeeds.

Usage::

    # Capture the @modelcontextprotocol/server-time catalog
    python scripts/capture_mcp_catalog.py \\
        --from-command "npx -y @modelcontextprotocol/server-time" \\
        --output examples/architectures/mcp_context_gateway/real_catalogs/time.json

    # Print to stdout (no file write):
    python scripts/capture_mcp_catalog.py \\
        --from-command "npx -y @modelcontextprotocol/server-time" \\
        --stdout

The output shape matches the committed snapshots:
``{"_source": <command>, "_captured_with": "<script>", "tools": [...]}``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shlex
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_OUTPUT_DIR = (
    _REPO_ROOT / "examples" / "architectures" / "mcp_context_gateway" / "real_catalogs"
)


def _tool_to_dict(tool: Any) -> dict[str, Any]:  # noqa: ANN401 — MCP SDK Tool model
    """Project an MCP SDK ``Tool`` pydantic model to the wire dict shape."""
    name = getattr(tool, "name", None)
    if not name:
        raise ValueError("upstream returned a tool with no name")
    payload: dict[str, Any] = {"name": str(name)}
    description = getattr(tool, "description", None)
    if description:
        payload["description"] = str(description)
    input_schema = getattr(tool, "inputSchema", None)
    if isinstance(input_schema, dict):
        payload["inputSchema"] = dict(input_schema)
    output_schema = getattr(tool, "outputSchema", None)
    if isinstance(output_schema, dict):
        payload["outputSchema"] = dict(output_schema)
    return payload


async def _capture_via_stdio(command: list[str]) -> list[dict[str, Any]]:
    """Spawn *command* as an MCP stdio server and collect its tools/list."""
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError as exc:  # pragma: no cover — surfaced to CLI
        raise SystemExit(
            f"error: MCP SDK not importable ({exc}). "
            "Re-install contextweaver with `pip install -e '.[dev]'`."
        ) from exc

    params = StdioServerParameters(command=command[0], args=command[1:])
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        listing = await session.list_tools()
        return [_tool_to_dict(t) for t in listing.tools]


def _write_snapshot(out_path: Path, source: str, tools: list[dict[str, Any]]) -> None:
    """Write the snapshot JSON in the same shape the committed files use."""
    payload: dict[str, Any] = {
        "_source": source,
        "_captured_with": "scripts/capture_mcp_catalog.py",
        "tools": tools,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def main() -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from-command",
        required=True,
        help=(
            "Shell-quoted command that launches the upstream MCP server "
            'over stdio (e.g. "npx -y @modelcontextprotocol/server-time").'
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        help=(
            "Output file path. Defaults to "
            "examples/architectures/mcp_context_gateway/real_catalogs/<server>.json"
        ),
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print the snapshot to stdout instead of writing to disk.",
    )
    args = parser.parse_args()

    command = shlex.split(args.from_command)
    if not command:
        sys.stderr.write("error: --from-command must not be empty\n")
        return 2

    try:
        tools = asyncio.run(_capture_via_stdio(command))
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(
            f"error: failed to capture catalog via {command!r}: {exc}\n"
            "(committed snapshot, if any, was NOT modified)\n"
        )
        return 1

    payload: dict[str, Any] = {
        "_source": " ".join(shlex.quote(c) for c in command),
        "_captured_with": "scripts/capture_mcp_catalog.py",
        "tools": tools,
    }
    if args.stdout:
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=False) + "\n")
        return 0
    out_path = args.output or (_DEFAULT_OUTPUT_DIR / f"{command[-1].split('/')[-1]}.json")
    _write_snapshot(out_path, payload["_source"], tools)
    print(f"Captured {len(tools)} tools from {command!r} -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
