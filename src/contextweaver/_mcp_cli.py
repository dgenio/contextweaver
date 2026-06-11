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

    # Wheel-install users (no ``examples/`` directory) can point the CLI
    # at the catalog packaged inside the wheel. ``gateway_catalog_path()``
    # resolves a real on-disk path for both editable and zipped installs:
    python -c "from contextweaver.data import gateway_catalog_path; print(gateway_catalog_path())"
    contextweaver mcp serve --gateway --catalog "$CATALOG"  # CATALOG from above

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

from contextweaver._version import __version__
from contextweaver.adapters.gateway_catalog_diagnostics import catalog_diagnostic_summary
from contextweaver.adapters.mcp_gateway_server import McpGatewayServer
from contextweaver.adapters.mcp_proxy_server import McpProxyServer
from contextweaver.adapters.mcp_upstream import StubUpstream
from contextweaver.adapters.proxy_runtime import ExposureMode, ProxyRuntime
from contextweaver.diagnostics import (
    DiagnosticSink,
    JsonlDiagnosticSink,
    load_diagnostic_events,
    render_diagnostic_report,
    summarize_diagnostics,
)

logger = logging.getLogger("contextweaver.mcp_cli")


class _ServeMode(str, Enum):
    gateway = "gateway"
    proxy = "proxy"


class _ReportFormat(str, Enum):
    json = "json"
    markdown = "markdown"


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


_CONFIG_KEYS: frozenset[str] = frozenset(
    {
        "catalog",
        "mode",
        "top_k",
        "beam_width",
        "cache_stable",
        "name",
        "version",
        "diagnostics",
        "quiet",
    }
)
_TRUE_STRINGS: frozenset[str] = frozenset({"true", "1", "yes", "on"})
_FALSE_STRINGS: frozenset[str] = frozenset({"false", "0", "no", "off"})


def _coerce_config_bool(key: str, value: object) -> bool:
    """Coerce a config value to ``bool``, accepting common string spellings.

    Plain ``bool(value)`` is wrong for config files: ``bool("false")`` is
    ``True``, so a quoted JSON/YAML ``"false"`` would silently enable the flag.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        norm = value.strip().lower()
        if norm in _TRUE_STRINGS:
            return True
        if norm in _FALSE_STRINGS:
            return False
    raise typer.BadParameter(f"{key} must be a boolean, got {value!r}", param_hint="--config")


def _coerce_config_int(key: str, value: object) -> int:
    """Coerce a config value to ``int`` (rejecting bools and non-numeric strings)."""
    # bool is an int subclass; a YAML ``top_k: true`` is a mistake, not 1.
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise typer.BadParameter(f"{key} must be an integer, got {value!r}", param_hint="--config")
    try:
        return int(value)
    except ValueError as exc:
        raise typer.BadParameter(
            f"{key} must be an integer, got {value!r}", param_hint="--config"
        ) from exc


def _load_serve_config(config_path: Path) -> dict[str, Any]:
    """Load a single ``mcp serve`` config file (one file, no Python authoring).

    The config is a JSON/YAML mapping whose keys mirror the ``serve`` options:
    ``catalog`` (path, required), ``mode`` (``gateway`` | ``proxy``),
    ``top_k``, ``beam_width``, ``cache_stable``, ``name``, ``version``,
    ``diagnostics``, and ``quiet``. Explicit CLI flags still win over config
    values; the file supplies everything else so a drop-in proxy can be launched
    with ``mcp serve --config gateway.yaml``. Relative catalog and diagnostics
    paths are resolved from the config file's directory.

    Args:
        config_path: Filesystem path to the config file (``.json``/``.yaml``/``.yml``).

    Returns:
        A validated dict of recognised config keys.

    Raises:
        typer.BadParameter: If the file is missing, unparseable, not a mapping,
            carries unknown keys, or omits ``catalog``.
    """
    if not config_path.exists():
        raise typer.BadParameter(f"config file not found: {config_path}", param_hint="--config")
    suffix = config_path.suffix.lower()
    if suffix in (".yaml", ".yml"):
        parse: Any = yaml.safe_load
    elif suffix == ".json":
        parse = json.loads
    else:
        raise typer.BadParameter(
            f"unsupported config format {suffix!r} for {config_path}; use .json, .yaml, or .yml",
            param_hint="--config",
        )
    try:
        data: Any = parse(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError, json.JSONDecodeError) as exc:
        raise typer.BadParameter(
            f"invalid config file {config_path}: {exc}", param_hint="--config"
        ) from exc
    if not isinstance(data, dict):
        raise typer.BadParameter(
            f"config file {config_path} must be a JSON/YAML mapping", param_hint="--config"
        )
    unknown = sorted(set(data) - _CONFIG_KEYS)
    if unknown:
        allowed = ", ".join(sorted(_CONFIG_KEYS))
        raise typer.BadParameter(
            f"unknown config key(s): {', '.join(unknown)}; allowed: {allowed}",
            param_hint="--config",
        )
    if "catalog" not in data:
        raise typer.BadParameter(
            f"config file {config_path} must set 'catalog'", param_hint="--config"
        )
    catalog_path = Path(str(data["catalog"])).expanduser()
    if not catalog_path.is_absolute():
        catalog_path = config_path.parent / catalog_path
    data["catalog"] = str(catalog_path.resolve())
    # Normalise + validate option types so `serve` can consume them directly
    # and `--config` parsing matches CLI flag semantics (e.g. a quoted
    # "false" must not become True).
    if "mode" in data and str(data["mode"]) not in ("gateway", "proxy"):
        raise typer.BadParameter(
            f"mode must be 'gateway' or 'proxy', got {data['mode']!r}", param_hint="--config"
        )
    for int_key in ("top_k", "beam_width"):
        if int_key in data:
            data[int_key] = _coerce_config_int(int_key, data[int_key])
    for bool_key in ("cache_stable", "quiet"):
        if bool_key in data:
            data[bool_key] = _coerce_config_bool(bool_key, data[bool_key])
    if "diagnostics" in data:
        diagnostics_path = Path(str(data["diagnostics"])).expanduser()
        if not diagnostics_path.is_absolute():
            diagnostics_path = config_path.parent / diagnostics_path
        data["diagnostics"] = str(diagnostics_path.resolve())
    return data


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
    if suffix in (".yaml", ".yml"):
        parse: Any = yaml.safe_load
    elif suffix == ".json":
        parse = json.loads
    else:
        raise typer.BadParameter(
            f"unsupported catalog format {suffix!r} for {catalog_path}; use .json, .yaml, or .yml",
            param_hint="--catalog",
        )
    try:
        data: Any = parse(text)
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        raise typer.BadParameter(
            f"invalid catalog file {catalog_path}: {exc}", param_hint="--catalog"
        ) from exc
    # Accept the real-MCP-server snapshot shape used by the recipes
    # ({"_source": ..., "tools": [...]}) by unwrapping its ``tools`` list.
    if isinstance(data, dict) and isinstance(data.get("tools"), list):
        data = data["tools"]
    if not isinstance(data, list) or not data:
        raise typer.BadParameter(
            f"catalog file {catalog_path} must be a non-empty sequence of tool entries "
            "(or a snapshot object with a non-empty 'tools' list)",
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
    diagnostic_sink: DiagnosticSink | None = None,
) -> ProxyRuntime:
    """Construct a :class:`ProxyRuntime` populated from *catalog_path*.

    Args:
        catalog_path: Catalog file path (JSON or YAML).
        mode: ``gateway`` or ``proxy`` exposure mode.
        top_k: Maximum number of cards returned by ``tool_browse``.
        beam_width: Router beam width.
        cache_stable: Toggle cache-stable browse ordering.
        diagnostic_sink: Optional structured event destination.

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
        diagnostic_sink=diagnostic_sink,
    )
    runtime.register_tool_defs_sync(tool_defs)
    # Reuse the helper so test code can introspect the resulting catalog
    # without re-parsing the file.
    return runtime


@mcp_app.command("inspect")
def inspect_catalog(
    catalog: Annotated[
        Path, typer.Option(..., "--catalog", help="Path to a JSON/YAML tool catalog.")
    ],
    mode: Annotated[
        _ServeMode, typer.Option("--mode", help="Exposure mode used for savings estimates.")
    ] = _ServeMode.gateway,
    format: Annotated[  # noqa: A002
        _ReportFormat, typer.Option("--format", help="Output format.")
    ] = _ReportFormat.markdown,
) -> None:
    """Inspect catalog size, namespaces, and static schema savings."""
    tool_defs = _load_tool_defs_from_catalog(catalog)
    runtime = ProxyRuntime(
        StubUpstream(tool_defs, handler=_stub_handler),
        mode=ExposureMode.GATEWAY if mode == _ServeMode.gateway else ExposureMode.TRANSPARENT,
    )
    runtime.register_tool_defs_sync(tool_defs)
    items = runtime.catalog.all()
    raw_defs = {item.id: raw for item, raw in zip(items, tool_defs, strict=True)}
    summary = catalog_diagnostic_summary(items, raw_defs, mode=mode.value)
    summary["catalog"] = str(catalog)
    summary["tool_ids"] = runtime.list_tool_ids()
    if format == _ReportFormat.json:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))
        return
    lines = [
        "# MCP Catalog Inspection",
        "",
        f"- Catalog: `{catalog}`",
        f"- Mode: {mode.value}",
        f"- Upstream tools: {summary['tool_count']}",
        f"- Exposed tools: {summary['exposed_tool_count']}",
        f"- Full schema tokens: {summary['full_schema_tokens']}",
        f"- Exposed schema tokens: {summary['exposed_schema_tokens']}",
        f"- Schema tokens avoided: {summary['schema_tokens_avoided']}",
        "",
        "## Namespaces",
    ]
    for namespace, count_value in summary["namespace_counts"].items():
        lines.append(f"- `{namespace or '(default)'}`: {count_value}")
    typer.echo("\n".join(lines))


@mcp_app.command("stats")
def diagnostic_stats(
    events: Annotated[Path, typer.Option(..., "--events", help="Diagnostic JSONL file.")],
    format: Annotated[  # noqa: A002
        _ReportFormat, typer.Option("--format", help="Output format.")
    ] = _ReportFormat.markdown,
) -> None:
    """Aggregate gateway event counts, savings, failures, and latency."""
    summary = summarize_diagnostics(load_diagnostic_events(events))
    if format == _ReportFormat.json:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))
    else:
        typer.echo(render_diagnostic_report(summary), nl=False)


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
    ctx: typer.Context,
    catalog: Annotated[
        Path | None,
        typer.Option(
            "--catalog",
            help="Path to a JSON/YAML tool catalog (contextweaver native or MCP shape).",
        ),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            help=(
                "Path to a single JSON/YAML config file supplying catalog + serve "
                "options (top_k, beam_width, mode, ...). Explicit CLI flags win. "
                "Enables a zero-Python drop-in launch: 'mcp serve --config gateway.yaml'."
            ),
        ),
    ] = None,
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
        typer.Option(
            "--version",
            help=(
                "MCP server version advertised on init. Defaults to the installed "
                "contextweaver package version."
            ),
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Validate catalog and print summary; do not bind stdio.",
        ),
    ] = False,
    diagnostics: Annotated[
        Path | None,
        typer.Option(
            "--diagnostics",
            help="Append sanitized gateway events to this JSONL file.",
        ),
    ] = None,
    quiet: Annotated[
        bool,
        typer.Option(
            "--quiet/--no-quiet",
            help="Suppress lifecycle messages on stderr.",
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

    # Config file fills any option not passed explicitly on the command line.
    if config is not None:
        cfg = _load_serve_config(config)

        def _from_cli(param: str) -> bool:
            source = ctx.get_parameter_source(param)
            return source is not None and source.name == "COMMANDLINE"

        # cfg values are already type-normalised + validated by
        # _load_serve_config, so consume them directly.
        if catalog is None and not _from_cli("catalog"):
            catalog = Path(str(cfg["catalog"]))
        if not _from_cli("mode") and "mode" in cfg:
            mode = _ServeMode(str(cfg["mode"]))
        if not _from_cli("top_k") and "top_k" in cfg:
            top_k = cfg["top_k"]
        if not _from_cli("beam_width") and "beam_width" in cfg:
            beam_width = cfg["beam_width"]
        if not _from_cli("cache_stable") and "cache_stable" in cfg:
            cache_stable = cfg["cache_stable"]
        if not _from_cli("name") and "name" in cfg:
            name = str(cfg["name"])
        if not _from_cli("version") and "version" in cfg:
            version = str(cfg["version"])
        if not _from_cli("diagnostics") and "diagnostics" in cfg:
            diagnostics = Path(str(cfg["diagnostics"]))
        if not _from_cli("quiet") and "quiet" in cfg:
            quiet = cfg["quiet"]

    if catalog is None:
        raise typer.BadParameter(
            "provide a catalog via --catalog or a config file via --config",
            param_hint="--catalog",
        )
    resolved_mode = _ServeMode.gateway if gateway else _ServeMode.proxy if proxy else mode

    # Advertise the installed contextweaver version on MCP ``initialize`` unless
    # an explicit version was supplied via ``--version`` or the config file.
    if version is None:
        version = __version__

    diagnostic_sink = JsonlDiagnosticSink(diagnostics) if diagnostics is not None else None
    runtime = _build_runtime(
        catalog,
        mode=resolved_mode,
        top_k=top_k,
        beam_width=beam_width,
        cache_stable=cache_stable,
        diagnostic_sink=diagnostic_sink,
    )
    tool_count = len(runtime.list_tool_ids())

    if not quiet:
        typer.echo(
            f"contextweaver mcp serve: mode={resolved_mode.value} "
            f"catalog={catalog} tools={tool_count} top_k={top_k} "
            f"beam_width={beam_width} cache_stable={cache_stable} "
            f"version={version} diagnostics={diagnostics or 'off'}",
            err=True,
        )

    if dry_run:
        if not quiet:
            typer.echo("dry-run: catalog validated; not binding stdio.", err=True)
        raise typer.Exit(0)

    server: McpGatewayServer | McpProxyServer
    if resolved_mode == _ServeMode.gateway:
        server = McpGatewayServer(runtime, name=name, version=version)
    else:
        server = McpProxyServer(runtime, name=name, version=version)

    async def _serve() -> None:
        await server.run_stdio()

    # Create the loop *before* the try so that a failure here surfaces as a
    # real exception rather than tripping ``UnboundLocalError`` from the
    # ``finally: loop.close()`` below.
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        _install_sigint_handler(loop)
        loop.run_until_complete(_serve())
    except (KeyboardInterrupt, asyncio.CancelledError):
        if not quiet:
            typer.echo("contextweaver mcp serve: interrupted, shutting down.", err=True)
        raise typer.Exit(0) from None
    finally:
        loop.close()


# Re-exported for tests / advanced wiring.
__all__ = [
    "mcp_app",
    "_load_tool_defs_from_catalog",
    "_load_serve_config",
    "_build_runtime",
    "_ServeMode",
]
