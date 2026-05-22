"""``contextweaver mcp serve`` CLI sub-app (issues #243, #246).

Wraps :mod:`contextweaver.adapters.mcp_gateway_server` and
:mod:`contextweaver.adapters.mcp_proxy_server` so they can be started from
the command line without writing a Python entrypoint script.

The sub-app is mounted onto the main Typer app from
:mod:`contextweaver.__main__`. It is intentionally a separate module so the
top-level CLI stays under the 600-line soft cap.

Usage::

    contextweaver mcp serve --gateway --catalog examples/sample_catalog.json
    contextweaver mcp serve --proxy   --catalog examples/sample_catalog.json

Both modes block on stdio and exit cleanly on ``SIGINT`` / EOF from the
client. The sub-app is marked **experimental** in ``--help`` for v0.9 and
will be promoted to stable once the wire shape is exercised by downstream
clients (Claude Desktop, GitHub Copilot, etc.).
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml

from contextweaver.adapters.mcp_gateway_server import McpGatewayServer
from contextweaver.adapters.mcp_proxy_server import McpProxyServer
from contextweaver.adapters.mcp_upstream import StubUpstream
from contextweaver.adapters.proxy_runtime import ExposureMode, ProxyRuntime

logger = logging.getLogger("contextweaver.mcp_cli")


class _ServeMode(str, Enum):
    gateway = "gateway"
    proxy = "proxy"


mcp_app = typer.Typer(
    name="mcp",
    help=(
        "[experimental] MCP server entrypoints.\n\n"
        "Boot a contextweaver gateway or transparent proxy over stdio so a "
        "downstream MCP client (Claude Desktop, Copilot, custom agents) can "
        "consume a bounded ChoiceCard list instead of every upstream tool."
    ),
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)


def _load_tool_defs_from_catalog(catalog_path: Path) -> list[dict[str, Any]]:
    """Read a contextweaver catalog file and return MCP-shaped tool defs.

    The catalog can be either the contextweaver-native JSON/YAML format
    (``id``/``kind``/``name``/``description``/optional ``args_schema``) or
    a raw MCP ``tools/list`` snapshot (``name``/``description``/``inputSchema``).
    Both shapes are recognised; the contextweaver shape is converted to the
    MCP shape on the fly because that is what
    :meth:`ProxyRuntime.register_tool_defs_sync` consumes.

    Args:
        catalog_path: Filesystem path to the catalog file (``.json``,
            ``.yaml``, or ``.yml``).

    Returns:
        A list of MCP-shaped tool definition dicts ready to feed into
        :meth:`ProxyRuntime.register_tool_defs_sync`.

    Raises:
        typer.BadParameter: If the file cannot be read or parsed, or if no
            tool entries can be found inside it.
    """
    if not catalog_path.exists():
        raise typer.BadParameter(f"catalog file not found: {catalog_path}", param_hint="--catalog")
    try:
        text = catalog_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise typer.BadParameter(
            f"cannot read catalog file {catalog_path}: {exc}", param_hint="--catalog"
        ) from exc
    suffix = catalog_path.suffix.lower()
    try:
        data: Any = yaml.safe_load(text) if suffix in (".yaml", ".yml") else json.loads(text)
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        raise typer.BadParameter(
            f"invalid catalog file {catalog_path}: {exc}", param_hint="--catalog"
        ) from exc
    if not isinstance(data, list) or not data:
        raise typer.BadParameter(
            f"catalog file {catalog_path} must be a non-empty sequence of tool entries",
            param_hint="--catalog",
        )

    mcp_defs: list[dict[str, Any]] = []
    for index, raw in enumerate(data):
        if not isinstance(raw, dict):
            raise typer.BadParameter(
                f"catalog entry {index} is not a mapping", param_hint="--catalog"
            )
        if "inputSchema" in raw and "name" in raw:
            # Already MCP-shaped — pass through with a defensive copy.
            mcp_defs.append(dict(raw))
            continue
        if {"id", "kind", "name", "description"}.issubset(raw):
            # Native contextweaver shape — promote ``args_schema`` to
            # ``inputSchema`` for the MCP boundary.
            schema = raw.get("args_schema") or {"type": "object"}
            mcp_defs.append(
                {
                    "name": str(raw["id"]),
                    "description": str(raw["description"]),
                    "inputSchema": schema,
                    # Keep the canonical contextweaver id available so the
                    # gateway can register the same tool_id; the adapter
                    # falls back to ``name`` when this is absent.
                    "_contextweaver": {
                        "id": str(raw["id"]),
                        "kind": str(raw["kind"]),
                        "name": str(raw["name"]),
                        "namespace": str(raw.get("namespace", "")),
                        "tags": list(raw.get("tags", [])),
                    },
                }
            )
            continue
        raise typer.BadParameter(
            f"catalog entry {index} has neither MCP shape (name+inputSchema) nor "
            "native contextweaver shape (id+kind+name+description)",
            param_hint="--catalog",
        )
    return mcp_defs


async def _stub_handler(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Return a canned MCP-shaped tool result for the ``--stub`` upstream.

    Real deployments will replace :class:`StubUpstream` with a transport
    bridge (HTTP, websockets, another stdio MCP client). The stub is just
    enough for the CLI to be exercisable without a live upstream — and
    matches the ``examples/mcp_gateway_demo.py`` pattern that downstream
    docs already reference.
    """
    body = f"contextweaver stub upstream\ntool: {name}\nreceived_args: {sorted(args.keys())}"
    return {"content": [{"type": "text", "text": body}], "isError": False}


def _build_runtime(
    catalog_path: Path,
    *,
    mode: _ServeMode,
    top_k: int,
    beam_width: int,
    cache_stable: bool,
) -> ProxyRuntime:
    """Construct a :class:`ProxyRuntime` populated from *catalog_path*.

    Args:
        catalog_path: Catalog file path (JSON or YAML).
        mode: ``gateway`` or ``proxy`` exposure mode.
        top_k: Maximum number of cards returned by ``tool_browse``.
        beam_width: Router beam width.
        cache_stable: Toggle cache-stable browse ordering.

    Returns:
        A ready-to-serve :class:`ProxyRuntime`.
    """
    tool_defs = _load_tool_defs_from_catalog(catalog_path)
    exposure = ExposureMode.GATEWAY if mode == _ServeMode.gateway else ExposureMode.TRANSPARENT
    runtime = ProxyRuntime(
        StubUpstream(tool_defs, handler=_stub_handler),
        mode=exposure,
        top_k=top_k,
        beam_width=beam_width,
        cache_stable=cache_stable,
    )
    runtime.register_tool_defs_sync(tool_defs)
    # Reuse the helper so test code can introspect the resulting catalog
    # without re-parsing the file.
    return runtime


def _install_sigint_handler(loop: asyncio.AbstractEventLoop) -> None:
    """Make ``Ctrl+C`` cancel the server task cleanly rather than crashing."""

    def _handler() -> None:
        for task in asyncio.all_tasks(loop):
            task.cancel()

    try:
        loop.add_signal_handler(signal.SIGINT, _handler)
        loop.add_signal_handler(signal.SIGTERM, _handler)
    except (NotImplementedError, RuntimeError):
        # Signal handlers are not supported on Windows event loops; ignore.
        pass


@mcp_app.command("serve")
def serve(
    catalog: Annotated[
        Path,
        typer.Option(
            "--catalog",
            help="Path to a JSON/YAML tool catalog (contextweaver native or MCP shape).",
        ),
    ],
    mode: Annotated[
        _ServeMode,
        typer.Option(
            "--mode",
            help=(
                "Server mode: 'gateway' (3 meta-tools: browse/execute/view) "
                "or 'proxy' (stripped tool list, transparent passthrough)."
            ),
        ),
    ] = _ServeMode.gateway,
    gateway: Annotated[
        bool,
        typer.Option(
            "--gateway/--no-gateway",
            help="Shortcut for --mode gateway (overrides --mode when set).",
        ),
    ] = False,
    proxy: Annotated[
        bool,
        typer.Option(
            "--proxy/--no-proxy",
            help="Shortcut for --mode proxy (overrides --mode when set).",
        ),
    ] = False,
    top_k: Annotated[
        int, typer.Option("--top-k", help="Max ChoiceCards per browse.", min=1, max=50)
    ] = 10,
    beam_width: Annotated[
        int, typer.Option("--beam-width", help="Router beam width.", min=1, max=10)
    ] = 3,
    cache_stable: Annotated[
        bool,
        typer.Option(
            "--cache-stable/--no-cache-stable",
            help="Enable byte-stable browse prefix for prompt-cache hits.",
        ),
    ] = False,
    name: Annotated[
        str, typer.Option("--name", help="MCP server display name advertised on init.")
    ] = "contextweaver",
    version: Annotated[
        str | None,
        typer.Option("--version", help="Optional MCP server version string."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Validate catalog and print summary; do not bind stdio.",
        ),
    ] = False,
) -> None:
    """[experimental] Run contextweaver as an MCP server over stdio.

    The server stays in the foreground; press Ctrl+C (or close the client's
    stdio pipe) to exit. Use ``--dry-run`` to validate the catalog and
    print the server configuration without binding stdio (useful in CI
    smoke tests and ``docker build`` healthchecks).
    """
    # Mutually-exclusive shortcut flags resolve to a concrete mode.
    if gateway and proxy:
        raise typer.BadParameter(
            "--gateway and --proxy are mutually exclusive", param_hint="--gateway"
        )
    resolved_mode = _ServeMode.gateway if gateway else _ServeMode.proxy if proxy else mode

    runtime = _build_runtime(
        catalog,
        mode=resolved_mode,
        top_k=top_k,
        beam_width=beam_width,
        cache_stable=cache_stable,
    )
    tool_count = len(runtime.list_tool_ids())

    typer.echo(
        f"contextweaver mcp serve: mode={resolved_mode.value} "
        f"catalog={catalog} tools={tool_count} top_k={top_k} "
        f"beam_width={beam_width} cache_stable={cache_stable}",
        err=True,
    )

    if dry_run:
        typer.echo("dry-run: catalog validated; not binding stdio.", err=True)
        raise typer.Exit(0)

    server: McpGatewayServer | McpProxyServer
    if resolved_mode == _ServeMode.gateway:
        server = McpGatewayServer(runtime, name=name, version=version)
    else:
        server = McpProxyServer(runtime, name=name, version=version)

    async def _serve() -> None:
        await server.run_stdio()

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _install_sigint_handler(loop)
        loop.run_until_complete(_serve())
    except (KeyboardInterrupt, asyncio.CancelledError):
        typer.echo("contextweaver mcp serve: interrupted, shutting down.", err=True)
        raise typer.Exit(0) from None
    finally:
        loop.close()


# Re-exported for tests / advanced wiring.
__all__ = [
    "mcp_app",
    "_load_tool_defs_from_catalog",
    "_build_runtime",
    "_ServeMode",
]
