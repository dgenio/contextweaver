"""Minimal stdio launcher: contextweaver gateway in front of an MCP catalog.

Used by the Claude Desktop (#278) and GitHub Copilot (#279) recipes. Until
``contextweaver mcp serve`` (issue #246) lands, this script is the
copy-pasteable single-command launcher both client configs point at.

Usage::

    # 1) Stub catalog — useful for local validation, no real upstream needed.
    python examples/recipes/serve_gateway.py --stub

    # 2) Real catalog snapshot.
    python examples/recipes/serve_gateway.py --catalog \\
        examples/architectures/mcp_context_gateway/real_catalogs/filesystem.json

    # 3) Programmatic — wrap your own UpstreamCall.
    from examples.recipes.serve_gateway import build_runtime_from_snapshot
    runtime = build_runtime_from_snapshot("path/to/snapshot.json")

The launcher exits 0 on a clean client disconnect and non-zero on any
configuration or transport error. It writes only the MCP wire protocol
to stdout — all diagnostics go to stderr via ``logging``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

from contextweaver.adapters import ExposureMode, ProxyRuntime, StubUpstream
from contextweaver.adapters.mcp_gateway_server import McpGatewayServer

logger = logging.getLogger("contextweaver.examples.recipes.serve_gateway")


_STUB_TOOLS: list[dict[str, Any]] = [
    {
        "name": "echo",
        "description": (
            "Echoes back its input verbatim — used to validate that the gateway is reachable."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "now",
        "description": "Returns the gateway's local time as an ISO-8601 string.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "annotations": {"readOnlyHint": True},
    },
]


def _load_snapshot_tools(path: Path) -> list[dict[str, Any]]:
    """Read a real-catalog snapshot file and return its ``tools`` list.

    Accepts the on-disk shape used by ``scripts/capture_mcp_catalog.py``
    (a JSON object with at least a top-level ``tools`` list, plus
    optional ``_source`` / ``_captured_with`` provenance fields).

    Raises :class:`RuntimeError` (not :class:`SystemExit`) on malformed
    snapshots so :func:`main` can map the failure to ``return 1`` and
    programmatic callers do not have to special-case ``SystemExit``.
    """
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict) or "tools" not in payload:
        raise RuntimeError(f"{path}: malformed snapshot, expected JSON object with a 'tools' key")
    tools = payload["tools"]
    if not isinstance(tools, list):
        raise RuntimeError(f"{path}: 'tools' field is not a list")
    return tools


def build_runtime_from_snapshot(snapshot: str | Path) -> ProxyRuntime:
    """Build a :class:`ProxyRuntime` in GATEWAY mode from a snapshot file."""
    tools = _load_snapshot_tools(Path(snapshot))
    runtime = ProxyRuntime(StubUpstream(tools), mode=ExposureMode.GATEWAY)
    runtime.register_tool_defs_sync(tools)
    return runtime


def build_stub_runtime() -> ProxyRuntime:
    """Build a :class:`ProxyRuntime` with the built-in stub catalog."""
    runtime = ProxyRuntime(StubUpstream(_STUB_TOOLS), mode=ExposureMode.GATEWAY)
    runtime.register_tool_defs_sync(_STUB_TOOLS)
    return runtime


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="serve_gateway",
        description=(
            "Run a contextweaver MCP gateway over stdio. Used by the "
            "Claude Desktop / GitHub Copilot recipes until "
            "`contextweaver mcp serve` (issue #246) lands."
        ),
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--stub",
        action="store_true",
        help="Use the built-in 2-tool stub catalog (echo, now).",
    )
    src.add_argument(
        "--catalog",
        type=Path,
        help=(
            "Path to a real-catalog snapshot JSON file (e.g. "
            "examples/architectures/mcp_context_gateway/real_catalogs/filesystem.json)."
        ),
    )
    p.add_argument(
        "--name",
        default="contextweaver-recipes-gateway",
        help=(
            "MCP server name advertised during initialise (default: contextweaver-recipes-gateway)."
        ),
    )
    return p.parse_args(argv)


async def _run(runtime: ProxyRuntime, *, name: str) -> None:
    server = McpGatewayServer(runtime, name=name)
    await server.run_stdio()


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code.

    Argparse may still raise :class:`SystemExit` for missing required
    arguments -- that is normal CLI behaviour and propagates to the
    ``__main__`` boundary. Logic-level startup failures (malformed
    snapshot, IO errors) are caught and mapped to ``return 1`` so
    programmatic callers can rely on the documented ``int`` return.
    """
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)

    try:
        runtime = build_stub_runtime() if args.stub else build_runtime_from_snapshot(args.catalog)
    except Exception as exc:  # noqa: BLE001 -- startup failures should never reach stdout
        logger.error("startup failed: %s", exc)
        return 1

    logger.info(
        "contextweaver gateway ready (%d upstream tools, mode=%s)",
        len(runtime.list_tool_ids()),
        runtime.mode.name,
    )
    try:
        asyncio.run(_run(runtime, name=args.name))
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # noqa: BLE001 — log transport errors to stderr
        logger.error("gateway stopped with error: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
