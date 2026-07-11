"""Live multi-upstream MCP launch behaviour (#366/#368/#374).

Pairs with the pure-data config in
:mod:`contextweaver.adapters.upstream_config` (per-upstream spec) and
:mod:`contextweaver.adapters.startup_policy` (fault-tolerance policy). This
module owns the actual network/process I/O: connecting to each configured
:class:`~contextweaver.adapters.upstream_config.UpstreamSpec` over its
transport, wrapping the session in
:class:`~contextweaver.adapters.mcp_upstream.McpClientUpstream`, applying
namespace/include/exclude filtering, and composing the survivors behind
:class:`~contextweaver.adapters.mcp_upstream.MultiplexUpstream` under the
configured :class:`~contextweaver.adapters.startup_policy.StartupPolicy`.

Every *surviving* upstream's transport is entered into a caller-owned
:class:`contextlib.AsyncExitStack`, so a single ``await stack.aclose()`` tears
them all down; a *failed* connect is unwound in its own task before returning.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession
from mcp.client.session import MessageHandlerFnT
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client

from contextweaver.adapters.gateway_error import classify_upstream_exception, redact_upstream_detail
from contextweaver.adapters.mcp_upstream import McpClientUpstream, MultiplexUpstream
from contextweaver.adapters.startup_policy import (
    StartupPolicy,
    StartupReport,
    UpstreamStatus,
    detect_tool_name_collisions,
)
from contextweaver.adapters.upstream_config import UpstreamSpec, tool_matches_filters
from contextweaver.exceptions import UpstreamStartupError

logger = logging.getLogger("contextweaver.adapters.upstream_launch")


class NamespacedFilteredUpstream:
    """Wrap one :class:`~contextweaver.adapters.mcp_upstream.McpClientUpstream`.

    Applies :attr:`~UpstreamSpec.include_tools` / :attr:`~UpstreamSpec.exclude_tools`
    filtering and an optional :attr:`~UpstreamSpec.namespace` prefix at the
    :meth:`list_tools` boundary, before the tool defs ever reach
    :class:`~contextweaver.adapters.proxy_runtime.ProxyRuntime` (#368).

    Prefixing a tool's ``name`` with ``"{namespace}."`` is deliberately the
    *only* mechanism used: contextweaver's existing canonical-``tool_id``
    machinery (:func:`contextweaver.adapters.mcp.infer_namespace`) already
    infers the namespace from a dotted prefix, so no changes to
    :class:`~contextweaver.adapters.proxy_runtime.ProxyRuntime` are needed to
    make a configured namespace show up in routing/collision diagnostics.
    """

    def __init__(
        self,
        inner: McpClientUpstream,
        *,
        namespace: str = "",
        include_tools: tuple[str, ...] = (),
        exclude_tools: tuple[str, ...] = (),
    ) -> None:
        self._inner = inner
        self._namespace = namespace
        self._include_tools = include_tools
        self._exclude_tools = exclude_tools
        # Maps the namespaced name back to the upstream's original name, so
        # call_tool can forward the request the upstream actually recognises.
        self._name_map: dict[str, str] = {}

    def _matches(self, upstream_name: str) -> bool:
        return tool_matches_filters(
            upstream_name,
            include_tools=self._include_tools,
            exclude_tools=self._exclude_tools,
        )

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return the filtered, namespaced tool defs from the wrapped upstream."""
        tools = await self._inner.list_tools()
        out: list[dict[str, Any]] = []
        self._name_map.clear()
        for tool_def in tools:
            original_name = str(tool_def.get("name", ""))
            if not original_name or not self._matches(original_name):
                continue
            namespaced_name = (
                f"{self._namespace}.{original_name}" if self._namespace else original_name
            )
            self._name_map[namespaced_name] = original_name
            namespaced_def = dict(tool_def)
            namespaced_def["name"] = namespaced_name
            out.append(namespaced_def)
        return out

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Forward a call, translating the namespaced name back to the original."""
        original_name = self._name_map.get(tool_name, tool_name)
        return await self._inner.call_tool(original_name, arguments)


async def _connect(
    spec: UpstreamSpec,
    stack: AsyncExitStack,
    timeout: float,
    *,
    message_handler: MessageHandlerFnT | None = None,
) -> ClientSession:
    """Open *spec*'s transport, initialise a session, and return it.

    Dispatch is by ``spec.type``: ``stdio`` launches a child process; ``http``
    (streamable HTTP, the current MCP transport) and ``sse`` (legacy) connect to
    an already-running endpoint. The transport + session are entered into a
    *local* stack. On any failure that local stack is closed synchronously in
    *this* task before the exception propagates, so a failed stdio child's
    background writer cannot surface a ``BrokenResourceError`` group later while
    the shared *stack* unwinds — which would pre-empt the clean
    :class:`~contextweaver.exceptions.UpstreamStartupError` classification the
    caller depends on (issue #785). Teardown moves to *stack* only on success.

    When a stdio child dies mid-handshake, anyio's transport task group can
    deliver a :class:`asyncio.CancelledError` to ``initialize()`` *before* the
    underlying ``BrokenResourceError`` surfaces. Closing the local stack runs
    that task group's exit, which absorbs the internal cancel and re-raises the
    *concrete* transport failure; that concrete error is preferred over the
    cancellation so :func:`launch_upstreams`' ``except Exception`` classifies
    the upstream as failed instead of the bare ``CancelledError`` (a
    ``BaseException``) escaping and being mistaken for a user interrupt.
    """
    if spec.type == "stdio":
        transport = stdio_client(
            StdioServerParameters(
                command=spec.command or "", args=list(spec.args), env=dict(spec.env) or None
            )
        )
    elif spec.type == "http":
        transport = streamablehttp_client(spec.url or "", headers=dict(spec.headers) or None)
    else:
        transport = sse_client(spec.url or "", headers=dict(spec.headers) or None)
    local = AsyncExitStack()
    try:
        streams = await local.enter_async_context(transport)
        read, write = streams[0], streams[1]
        session = await local.enter_async_context(
            ClientSession(read, write, message_handler=message_handler)
        )
        await asyncio.wait_for(session.initialize(), timeout=timeout)
    except BaseException as connect_exc:
        try:
            await local.aclose()
        except Exception as teardown_exc:  # noqa: BLE001 — concrete transport failure
            raise teardown_exc from connect_exc
        raise
    stack.push_async_callback(local.aclose)
    return session


# All transports share one connect path (dispatch is by ``spec.type`` inside
# ``_open_transport``); the per-type keys stay so tests can monkeypatch a
# single transport's connector in isolation.
_CONNECTORS = {"stdio": _connect, "http": _connect, "sse": _connect}


async def launch_upstreams(
    specs: list[UpstreamSpec],
    policy: StartupPolicy,
    stack: AsyncExitStack,
    *,
    message_handler: MessageHandlerFnT | None = None,
) -> tuple[MultiplexUpstream, StartupReport]:
    """Connect every configured upstream and compose the survivors (#366/#374).

    Each upstream is connected under its own :attr:`~UpstreamSpec.timeout`-and
    :attr:`~StartupPolicy.upstream_timeout_seconds`-bounded attempt; a
    connect failure, timeout, or ``tools/list`` failure is recorded in the
    returned :class:`~contextweaver.adapters.startup_policy.StartupReport`
    rather than propagated, so one bad upstream cannot take down the others.
    Once every attempt has resolved, :attr:`~StartupPolicy.mode`,
    :attr:`~StartupPolicy.min_healthy_upstreams`, and
    :attr:`~StartupPolicy.fail_on_empty_catalog` are evaluated together and
    may raise :class:`~contextweaver.exceptions.UpstreamStartupError`.

    Args:
        specs: Configured upstreams, in declaration order.
        policy: The fault-tolerance policy governing this startup.
        stack: An :class:`contextlib.AsyncExitStack` the caller owns and will
            close (tearing down every connected transport) once serving ends.
        message_handler: Optional MCP ``ClientSession`` message handler threaded
            to every session created here (issue #424) — see
            :func:`contextweaver.adapters.live_refresh.make_message_handler`.

    Returns:
        ``(multiplex_upstream, report)`` — the multiplex is safe to hand to
        :class:`~contextweaver.adapters.proxy_runtime.ProxyRuntime` even when
        it wraps zero sources (its ``list_tools()`` then returns an empty
        list); callers that require a non-empty catalog rely on
        :attr:`~StartupPolicy.fail_on_empty_catalog` instead of checking this
        return value directly.

    Raises:
        UpstreamStartupError: If a required upstream fails under
            ``mode="strict"``, fewer than :attr:`~StartupPolicy.min_healthy_upstreams`
            upstreams started, or the effective catalog is empty and
            :attr:`~StartupPolicy.fail_on_empty_catalog` is set.
    """
    statuses: list[UpstreamStatus] = []
    sources: list[NamespacedFilteredUpstream] = []
    per_upstream_names: dict[str, list[str]] = {}

    for spec in specs:
        connector = _CONNECTORS[spec.type]
        try:
            # NOTE: do not wrap this call in asyncio.wait_for — the connector
            # enters context managers whose __aexit__ runs later when `stack`
            # closes, and anyio's cancel scopes require enter/exit in the same
            # Task (wait_for schedules a new one). The bounded step is
            # session.initialize() *inside* each connector. Keyword passed only
            # when set, so 3-argument test connectors keep working.
            kwargs: dict[str, Any] = (
                {} if message_handler is None else {"message_handler": message_handler}
            )
            session = await connector(spec, stack, policy.upstream_timeout_seconds, **kwargs)
        except asyncio.TimeoutError:
            logger.warning("upstream %r timed out during connect", spec.name)
            statuses.append(UpstreamStatus(name=spec.name, status="timed_out"))
            continue
        except Exception as exc:  # noqa: BLE001 — classified below, never re-raised here
            code, _ = classify_upstream_exception(exc)
            error = f"{code}: {redact_upstream_detail(str(exc))}"
            logger.warning("upstream %r failed to connect: %s", spec.name, error)
            statuses.append(UpstreamStatus(name=spec.name, status="failed", error=error))
            continue

        filtered = NamespacedFilteredUpstream(
            McpClientUpstream(session, timeout=spec.timeout),
            namespace=spec.namespace,
            include_tools=spec.include_tools,
            exclude_tools=spec.exclude_tools,
        )
        try:
            tool_defs = await asyncio.wait_for(
                filtered.list_tools(), timeout=policy.upstream_timeout_seconds
            )
        except asyncio.TimeoutError:
            logger.warning("upstream %r timed out listing tools", spec.name)
            statuses.append(UpstreamStatus(name=spec.name, status="timed_out"))
            continue
        except Exception as exc:  # noqa: BLE001
            code, _ = classify_upstream_exception(exc)
            error = f"{code}: {redact_upstream_detail(str(exc))}"
            logger.warning("upstream %r failed to list tools: %s", spec.name, error)
            statuses.append(UpstreamStatus(name=spec.name, status="failed", error=error))
            continue

        per_upstream_names[spec.name] = [str(t.get("name", "")) for t in tool_defs]
        sources.append(filtered)
        statuses.append(UpstreamStatus(name=spec.name, status="loaded", tool_count=len(tool_defs)))

    report = StartupReport(
        statuses=tuple(statuses),
        collisions=tuple(detect_tool_name_collisions(per_upstream_names)),
    )
    for line in report.render_lines():
        logger.info(line)

    required_by_name = {spec.name: spec.required for spec in specs}
    failed_required = [
        s.name for s in statuses if required_by_name.get(s.name, True) and s.status != "loaded"
    ]
    if policy.mode == "strict" and failed_required:
        raise UpstreamStartupError(
            f"required upstream(s) failed to start: {', '.join(failed_required)}", report=report
        )
    if report.healthy_count < policy.min_healthy_upstreams:
        raise UpstreamStartupError(
            f"only {report.healthy_count} upstream(s) started; "
            f"need >= {policy.min_healthy_upstreams}",
            report=report,
        )
    if report.total_tools == 0 and policy.fail_on_empty_catalog:
        raise UpstreamStartupError("effective upstream catalog is empty", report=report)

    return MultiplexUpstream(list(sources)), report


__all__ = [
    "NamespacedFilteredUpstream",
    "launch_upstreams",
]
