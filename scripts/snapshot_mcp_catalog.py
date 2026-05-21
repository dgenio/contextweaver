"""Snapshot a real MCP server's tools/list payload to a JSON file (issue #280).

Spawns an MCP server over stdio, issues a single ``tools/list`` request,
and writes the result to disk in the shape consumed by
``examples/architectures/mcp_context_gateway/main_real.py`` (``{_meta,
tools}``). Use this to refresh the committed reference snapshots when an
upstream server ships a new version, or to add a new snapshot.

Example::

    python scripts/snapshot_mcp_catalog.py \\
        --command "npx -y @modelcontextprotocol/server-filesystem /tmp" \\
        --source-name "@modelcontextprotocol/server-filesystem" \\
        --server-version 2025.10.1 \\
        --license MIT \\
        --output examples/architectures/mcp_context_gateway/real_catalogs/filesystem_mcp.json

The helper requires the optional ``mcp`` SDK (already a core dependency
of contextweaver). It uses ``asyncio.run`` and exits 0 on success, 1 on
any error.

The output JSON has two top-level keys:

- ``_meta`` — provenance: ``source``, ``server_package``,
  ``server_version``, ``license``, ``license_url``, ``snapshotted_at``,
  ``snapshot_method``, ``notes``.
- ``tools`` — verbatim list of ``tools/list`` entries, deduplicated by
  ``name``.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import json
import logging
import shlex
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger("contextweaver.scripts.snapshot_mcp_catalog")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="snapshot_mcp_catalog",
        description=(
            "Capture a real MCP server's tools/list as a JSON snapshot "
            "for examples/architectures/mcp_context_gateway/real_catalogs/."
        ),
    )
    p.add_argument(
        "--command",
        required=True,
        help=(
            "Shell-quoted stdio command that launches the upstream MCP "
            "server, e.g. `npx -y @modelcontextprotocol/server-filesystem /tmp`."
        ),
    )
    p.add_argument(
        "--source-name",
        required=True,
        help="Upstream server identifier (e.g. `@modelcontextprotocol/server-filesystem`).",
    )
    p.add_argument(
        "--server-version",
        default="unknown",
        help="Upstream server version string (default: 'unknown').",
    )
    p.add_argument(
        "--license",
        default="MIT",
        help="SPDX licence identifier of the upstream server (default: 'MIT').",
    )
    p.add_argument(
        "--license-url",
        default="",
        help="Stable URL to the upstream licence text.",
    )
    p.add_argument(
        "--notes",
        default="",
        help="Free-form notes embedded in _meta.notes.",
    )
    p.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Where to write the resulting snapshot JSON.",
    )
    p.add_argument(
        "--source",
        default="modelcontextprotocol/servers",
        help=(
            "Upstream project identifier for _meta.source "
            "(default: 'modelcontextprotocol/servers')."
        ),
    )
    return p.parse_args(argv)


async def _fetch_tools_list(command: str) -> list[dict[str, Any]]:
    """Connect to the MCP server defined by *command* and return its tools/list.

    The function spawns the server as a subprocess over stdio using the MCP
    SDK's :class:`mcp.client.stdio.stdio_client`, then issues a single
    ``tools/list`` request. The MCP SDK is a core dependency, so this
    helper does not need a guarded import.
    """
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError as exc:
        raise SystemExit(
            "The `mcp` SDK is required to snapshot a real MCP server. "
            "Install it with `pip install mcp` (already a core dependency)."
        ) from exc

    argv = shlex.split(command)
    if not argv:
        raise SystemExit("--command was empty after shell-quoting parse")
    params = StdioServerParameters(command=argv[0], args=argv[1:])

    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        response = await session.list_tools()
        raw_tools = response.tools

    serialised: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tool in raw_tools:
        # MCP SDK Tool objects expose `model_dump` (pydantic) — fall back to
        # dict() conversion for any custom transport that returns plain dicts.
        if hasattr(tool, "model_dump"):
            dumped = tool.model_dump(exclude_none=True)
        elif isinstance(tool, dict):
            dumped = dict(tool)
        else:
            dumped = {
                "name": getattr(tool, "name", ""),
                "description": getattr(tool, "description", ""),
                "inputSchema": getattr(tool, "inputSchema", {}),
            }
        name = str(dumped.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        serialised.append(dumped)
    return serialised


def _build_meta(args: argparse.Namespace) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "source": args.source,
        "server_package": args.source_name,
        "server_version": args.server_version,
        "license": args.license,
        "snapshotted_at": _dt.date.today().isoformat(),
        "snapshot_method": f"scripts/snapshot_mcp_catalog.py --command {args.command!r}",
    }
    if args.license_url:
        meta["license_url"] = args.license_url
    if args.notes:
        meta["notes"] = args.notes
    return meta


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger.info("snapshot: launching `%s`", args.command)

    try:
        tools = asyncio.run(_fetch_tools_list(args.command))
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — surface upstream failures verbatim
        logger.error("snapshot failed: %s", exc)
        return 1

    payload = {"_meta": _build_meta(args), "tools": tools}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n")
    logger.info("snapshot: wrote %d tools to %s", len(tools), args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
