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
from contextweaver.adapters.gateway_controls import RateLimiter, ToolResultCache
from contextweaver.adapters.gateway_policy import RateLimitPolicy, RetryPolicy
from contextweaver.adapters.gateway_primitives import PrimitiveGatewayRuntime
from contextweaver.adapters.mcp_gateway_server import McpGatewayServer
from contextweaver.adapters.mcp_primitive_upstream import StubPrimitiveUpstream
from contextweaver.adapters.mcp_proxy_server import McpProxyServer
from contextweaver.adapters.mcp_upstream import StubUpstream
from contextweaver.adapters.proxy_runtime import ExposureMode, ProxyRuntime
from contextweaver.context.manager import ContextManager
from contextweaver.diagnostics import (
    DiagnosticSink,
    JsonlDiagnosticSink,
    load_diagnostic_events,
    render_diagnostic_report,
    summarize_diagnostics,
)
from contextweaver.exceptions import ConfigError, ContextWeaverError
from contextweaver.store import JsonFileArtifactStore, SqliteEventLog, StoreBundle

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
        "state_dir",
        "retry",
        "rate_limits",
        "cache",
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
    if "state_dir" in data:
        state_dir_path = Path(str(data["state_dir"])).expanduser()
        if not state_dir_path.is_absolute():
            state_dir_path = config_path.parent / state_dir_path
        data["state_dir"] = str(state_dir_path.resolve())
    for mapping_key in ("retry", "rate_limits", "cache"):
        if mapping_key in data and not isinstance(data[mapping_key], dict):
            raise typer.BadParameter(f"{mapping_key} must be a mapping", param_hint="--config")
    return data


def _build_dispatch_controls(
    cfg: dict[str, Any],
) -> tuple[RetryPolicy | None, RateLimiter | None, ToolResultCache | None]:
    """Build the opt-in dispatch-path controls from config blocks (issues #529/#482/#512).

    Reads the optional ``retry``, ``rate_limits``, and ``cache`` config blocks
    and constructs the matching policy objects, validating them at startup.
    A ``cache`` block only builds a cache when ``read_only`` is truthy
    (caching is opt-in; the gateway still gates on the upstream read-only hint).

    Args:
        cfg: The validated config mapping from :func:`_load_serve_config`.

    Returns:
        ``(retry_policy, rate_limiter, result_cache)`` — each ``None`` when its
        block is absent.

    Raises:
        typer.BadParameter: If any block is malformed.
    """
    try:
        retry_policy = RetryPolicy.from_dict(cfg["retry"]) if "retry" in cfg else None
        rate_limiter = (
            RateLimiter(RateLimitPolicy.from_dict(cfg["rate_limits"]))
            if "rate_limits" in cfg
            else None
        )
        result_cache: ToolResultCache | None = None
        cache_cfg = cfg.get("cache")
        if isinstance(cache_cfg, dict) and _coerce_config_bool(
            "cache.read_only", cache_cfg.get("read_only", False)
        ):
            allow = cache_cfg.get("allow")
            if allow is not None and (
                isinstance(allow, str)
                or not isinstance(allow, (list, tuple))
                or not all(isinstance(item, str) for item in allow)
            ):
                # A bare string would otherwise become a set of characters.
                raise ConfigError("cache.allow must be a list of tool_id strings")
            result_cache = ToolResultCache(
                ttl_seconds=float(cache_cfg.get("ttl_seconds", 60.0)),
                max_entries=int(cache_cfg.get("max_entries", 256)),
                allow=frozenset(allow) if allow is not None else None,
            )
    except (ContextWeaverError, ValueError, TypeError) as exc:
        raise typer.BadParameter(str(exc), param_hint="--config") from exc
    return retry_policy, rate_limiter, result_cache


def _parse_catalog_file(catalog_path: Path) -> Any:  # noqa: ANN401 — JSON/YAML payload
    """Read and parse a JSON/YAML catalog file into its raw Python object.

    Args:
        catalog_path: Filesystem path to the catalog file (``.json``,
            ``.yaml``, or ``.yml``).

    Returns:
        The parsed payload (typically a list of entries or a snapshot dict).

    Raises:
        typer.BadParameter: If the file is missing, unreadable, has an
            unsupported extension, or cannot be parsed.
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
        return parse(text)
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        raise typer.BadParameter(
            f"invalid catalog file {catalog_path}: {exc}", param_hint="--catalog"
        ) from exc


def _collect_primitive_defs(raw: Any, *, kind: str, id_field: str) -> list[dict[str, Any]]:  # noqa: ANN401 — JSON/YAML payload
    """Return the valid dict entries from a snapshot ``resources``/``prompts`` list.

    Entries that are not dicts, or that lack the required identity field
    (``uri`` for resources, ``name`` for prompts), are skipped with a warning so
    a mistyped catalog entry is surfaced rather than silently dropped.

    Args:
        raw: The raw value under the snapshot's ``resources`` / ``prompts`` key.
        kind: Human-readable primitive kind for log messages (``"resource"`` /
            ``"prompt"``).
        id_field: The required identity key (``"uri"`` / ``"name"``).

    Returns:
        A list of well-formed MCP-shaped dicts.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            logger.warning("skipping non-dict %s catalog entry: %r", kind, entry)
            continue
        if not entry.get(id_field):
            logger.warning("skipping %s catalog entry missing %r: %r", kind, id_field, entry)
            continue
        out.append(dict(entry))
    return out


def _load_primitive_defs_from_catalog(
    catalog_path: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(resource_defs, prompt_defs)`` from a snapshot-shaped catalog (#669 / #670).

    Resources and prompts are optional siblings of ``tools`` in a snapshot
    object (``{"tools": [...], "resources": [...], "prompts": [...]}``). A bare
    list catalog (tools only) or a catalog without those keys yields two empty
    lists, so the gateway transparently runs tools-only when no primitives are
    declared. Malformed entries (non-dict, or missing the required ``uri`` /
    ``name`` identity field) are skipped with a warning rather than silently
    dropped, so a mistyped catalog entry does not vanish without a trace.

    Args:
        catalog_path: Filesystem path to the catalog file.

    Returns:
        A ``(resource_defs, prompt_defs)`` tuple of raw MCP-shaped dicts.
    """
    data = _parse_catalog_file(catalog_path)
    if not isinstance(data, dict):
        return [], []
    resources = _collect_primitive_defs(data.get("resources"), kind="resource", id_field="uri")
    prompts = _collect_primitive_defs(data.get("prompts"), kind="prompt", id_field="name")
    return resources, prompts


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
    data: Any = _parse_catalog_file(catalog_path)
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


def _build_state_stores(state_dir: Path) -> StoreBundle:
    """Build persistent stores rooted at *state_dir* for ``mcp serve`` (issue #511).

    Lays out ``{state_dir}/events.sqlite3`` (a :class:`SqliteEventLog`) and
    ``{state_dir}/artifacts/`` (a :class:`JsonFileArtifactStore`).  Both
    backends re-instantiate against existing files/directories, so pointing a
    restarted server at the same *state_dir* rehydrates prior event history and
    keeps previously-issued artifact handles resolvable via ``tool_view``.

    Args:
        state_dir: Directory to persist gateway state under (created if absent).

    Returns:
        A :class:`StoreBundle` wiring the persistent event log + artifact store.

    Raises:
        typer.BadParameter: If *state_dir* cannot be created or written.
    """
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        event_log = SqliteEventLog(state_dir / "events.sqlite3")
        artifact_store = JsonFileArtifactStore(state_dir / "artifacts")
    except OSError as exc:
        raise typer.BadParameter(
            f"state_dir {state_dir} is not writable: {exc}", param_hint="--state-dir"
        ) from exc
    return StoreBundle(event_log=event_log, artifact_store=artifact_store)


def _build_runtime(
    catalog_path: Path,
    *,
    mode: _ServeMode,
    top_k: int,
    beam_width: int,
    cache_stable: bool,
    diagnostic_sink: DiagnosticSink | None = None,
    state_dir: Path | None = None,
    retry_policy: RetryPolicy | None = None,
    rate_limiter: RateLimiter | None = None,
    result_cache: ToolResultCache | None = None,
) -> ProxyRuntime:
    """Construct a :class:`ProxyRuntime` populated from *catalog_path*.

    Args:
        catalog_path: Catalog file path (JSON or YAML).
        mode: ``gateway`` or ``proxy`` exposure mode.
        top_k: Maximum number of cards returned by ``tool_browse``.
        beam_width: Router beam width.
        cache_stable: Toggle cache-stable browse ordering.
        diagnostic_sink: Optional structured event destination.
        state_dir: Optional directory for persistent gateway state (issue
            #511).  When set, the runtime's :class:`ContextManager` is wired
            with file-backed stores so artifact handles and event history
            survive a restart; when ``None`` (default), in-memory stores are
            used and state is lost on exit.
        retry_policy: Optional upstream-retry policy (issue #529).
        rate_limiter: Optional per-session quota enforcer (issue #482).
        result_cache: Optional read-only response cache (issue #512).

    Returns:
        A ready-to-serve :class:`ProxyRuntime`.
    """
    tool_defs = _load_tool_defs_from_catalog(catalog_path)
    exposure = ExposureMode.GATEWAY if mode == _ServeMode.gateway else ExposureMode.TRANSPARENT
    context_manager = (
        ContextManager(stores=_build_state_stores(state_dir)) if state_dir is not None else None
    )
    runtime = ProxyRuntime(
        StubUpstream(tool_defs, handler=_stub_handler),
        mode=exposure,
        top_k=top_k,
        beam_width=beam_width,
        cache_stable=cache_stable,
        diagnostic_sink=diagnostic_sink,
        context_manager=context_manager,
        retry_policy=retry_policy,
        rate_limiter=rate_limiter,
        result_cache=result_cache,
    )
    runtime.register_tool_defs_sync(tool_defs)
    # Reuse the helper so test code can introspect the resulting catalog
    # without re-parsing the file.
    return runtime


def _build_primitive_runtime(
    catalog_path: Path,
    runtime: ProxyRuntime,
    *,
    top_k: int,
    beam_width: int,
) -> PrimitiveGatewayRuntime | None:
    """Construct a :class:`PrimitiveGatewayRuntime` from *catalog_path* (#669 / #670).

    Resources and prompts are read from the optional ``resources`` / ``prompts``
    keys of a snapshot-shaped catalog. When neither is present, the gateway runs
    tools-only and this returns ``None`` (so ``McpGatewayServer`` advertises only
    the tool meta-tools).

    The primitive runtime shares the tool runtime's
    :class:`~contextweaver.context.manager.ContextManager`, so resource/prompt
    reads land in the same artifact store and are addressable via ``tool_view``.
    Without a live upstream attached, a :class:`StubPrimitiveUpstream` serves the
    declared listings and canned reads, mirroring the tool path's
    :class:`StubUpstream`.

    Args:
        catalog_path: Catalog file path (JSON or YAML).
        runtime: The already-built tool :class:`ProxyRuntime` to share state with.
        top_k: Maximum number of cards returned by ``resource_browse`` /
            ``prompt_browse``.
        beam_width: Router beam width.

    Returns:
        A ready :class:`PrimitiveGatewayRuntime`, or ``None`` when the catalog
        declares no resources or prompts.
    """
    resource_defs, prompt_defs = _load_primitive_defs_from_catalog(catalog_path)
    if not resource_defs and not prompt_defs:
        return None
    primitive_runtime = PrimitiveGatewayRuntime(
        StubPrimitiveUpstream(resource_defs, prompt_defs),
        context_manager=runtime.context_manager,
        beam_width=beam_width,
        top_k=top_k,
    )
    primitive_runtime.register_sync(resource_defs, prompt_defs)
    return primitive_runtime


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
    state_dir: Annotated[
        Path | None,
        typer.Option(
            "--state-dir",
            help=(
                "Persist gateway state under this directory so artifact handles "
                "and event history survive a restart (events.sqlite3 + artifacts/). "
                "Omit for the zero-config in-memory default."
            ),
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

    # Opt-in dispatch-path controls are config-file only (no CLI flags); they
    # stay None unless a config block supplies them.
    retry_policy: RetryPolicy | None = None
    rate_limiter: RateLimiter | None = None
    result_cache: ToolResultCache | None = None

    # Config file fills any option not passed explicitly on the command line.
    if config is not None:
        cfg = _load_serve_config(config)
        retry_policy, rate_limiter, result_cache = _build_dispatch_controls(cfg)

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
        if not _from_cli("state_dir") and "state_dir" in cfg:
            state_dir = Path(str(cfg["state_dir"]))
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
        state_dir=state_dir,
        retry_policy=retry_policy,
        rate_limiter=rate_limiter,
        result_cache=result_cache,
    )
    tool_count = len(runtime.list_tool_ids())

    # In gateway mode, also surface upstream resources/prompts (#669 / #670) when
    # the catalog declares them, sharing the tool runtime's ContextManager so
    # reads land in one artifact store / tool_view surface. Proxy mode is a
    # transparent tool passthrough and does not expose the primitive meta-tools.
    primitive_runtime: PrimitiveGatewayRuntime | None = None
    if resolved_mode == _ServeMode.gateway:
        primitive_runtime = _build_primitive_runtime(
            catalog, runtime, top_k=top_k, beam_width=beam_width
        )

    if not quiet:
        primitives = (
            "off"
            if primitive_runtime is None
            else (
                f"resources={len(primitive_runtime.resource_ids())} "
                f"prompts={len(primitive_runtime.prompt_ids())}"
            )
        )
        typer.echo(
            f"contextweaver mcp serve: mode={resolved_mode.value} "
            f"catalog={catalog} tools={tool_count} primitives={primitives} top_k={top_k} "
            f"beam_width={beam_width} cache_stable={cache_stable} "
            f"version={version} diagnostics={diagnostics or 'off'} "
            f"state_dir={state_dir or 'in-memory'}",
            err=True,
        )

    if dry_run:
        if not quiet:
            typer.echo("dry-run: catalog validated; not binding stdio.", err=True)
        raise typer.Exit(0)

    server: McpGatewayServer | McpProxyServer
    if resolved_mode == _ServeMode.gateway:
        server = McpGatewayServer(
            runtime, name=name, version=version, primitive_runtime=primitive_runtime
        )
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
