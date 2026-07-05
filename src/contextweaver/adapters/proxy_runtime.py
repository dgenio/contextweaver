"""Shared core for the MCP proxy (#13) and gateway (#28) runtime modes.

:class:`ProxyRuntime` is the internal subsystem that both the transparent
proxy and the two-tool gateway share, as called for by ``docs/gateway_spec.md``
§4.  It owns:

- Upstream MCP catalog aggregation via an injected
  :class:`UpstreamCall` Protocol (no MCP transport coupling).
- A per-session :class:`~contextweaver.context.manager.ContextManager`
  with the standard 4-store bundle.
- A :class:`~contextweaver.routing.catalog.Catalog` +
  :class:`~contextweaver.routing.graph.ChoiceGraph` rebuilt whenever the
  upstream catalog changes.
- The gateway's three primitives:

  * :meth:`ProxyRuntime.browse` — ``tool_browse(query|path)`` per §3.
  * :meth:`ProxyRuntime.execute` — ``tool_execute(tool_id, args)`` per
    §4.4 (validates args against the hydrated schema before upstream
    dispatch, then runs the result through the context firewall).
  * :meth:`ProxyRuntime.view` — ``tool_view(handle, selector)`` per #34
    (drilldown over a previously-stored artifact).

- The proxy's additional helpers:

  * :meth:`ProxyRuntime.strip_tools_list` — stripped MCP-format
    ``tools/list`` per §4.1.
  * :meth:`ProxyRuntime.hydrate` — `Catalog.hydrate` wrapper exposed as
    ``tool_hydrate(tool_id)`` per §4.1.

The shared error shape is :class:`~contextweaver.adapters.gateway_error.GatewayError`;
none of these primitives raise across the MCP boundary.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from enum import Enum
from time import perf_counter
from typing import Any, Literal, Protocol, runtime_checkable

import jsonschema.exceptions

from contextweaver.adapters._proxy_dispatch import (
    UpstreamNameIndex,
    build_dry_run_report,
    execute_policy_error,
    persist_result_artifacts,
    rate_limited_error,
    view_policy_error,
)
from contextweaver.adapters.gateway_args import Repair, normalize_args
from contextweaver.adapters.gateway_authz import ToolPolicy
from contextweaver.adapters.gateway_controls import (
    RateLimitDecision,
    RateLimiter,
    Sleeper,
    ToolResultCache,
    call_with_retry,
)
from contextweaver.adapters.gateway_diagnostics import GatewayTelemetry
from contextweaver.adapters.gateway_error import (
    GatewayError,
    classify_upstream_exception,
    redact_upstream_detail,
)
from contextweaver.adapters.gateway_policy import DryRunReport, RetryPolicy
from contextweaver.adapters.gateway_validation import (
    DEFAULT_SCHEMA_LIMITS,
    CatalogRefreshReport,
    SchemaLimits,
    SchemaValidator,
    SkippedTool,
    build_validator,
    check_schema_health,
)
from contextweaver.adapters.mcp import mcp_result_to_envelope, mcp_tool_to_selectable
from contextweaver.context.manager import ContextManager
from contextweaver.diagnostics import DiagnosticSink
from contextweaver.envelope import ChoiceCard, HydrationResult, ResultEnvelope
from contextweaver.exceptions import (
    ArtifactNotFoundError,
    CatalogError,
    ContextWeaverError,
    ItemNotFoundError,
    PathInvalidError,
    PathNotFoundError,
)
from contextweaver.routing.cards import bound_browse_response, item_to_card, make_choice_cards
from contextweaver.routing.catalog import Catalog
from contextweaver.routing.graph import ChoiceGraph
from contextweaver.routing.path import parse_path, resolve_path
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.store.protocols import ArtifactStore
from contextweaver.types import SelectableItem

logger = logging.getLogger("contextweaver.adapters.proxy_runtime")


class ExposureMode(str, Enum):
    """Which agent-facing surface the runtime is wired into (§4)."""

    TRANSPARENT = "transparent"
    GATEWAY = "gateway"


@runtime_checkable
class UpstreamCall(Protocol):
    """Transport-agnostic interface to one or more upstream MCP servers.

    Implementations may fan out over a single server, multiple servers,
    or an in-process stub.  The :class:`ProxyRuntime` only depends on
    the two methods below — concrete MCP-SDK wiring lives in
    :mod:`contextweaver.adapters.mcp_upstream`.
    """

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return MCP-format tool definitions from all upstream servers."""
        ...

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke *tool_name* upstream and return the raw MCP result dict.

        The returned dict matches the MCP wire format consumed by
        :func:`~contextweaver.adapters.mcp.mcp_result_to_envelope`.
        Implementations MUST translate transport-level errors into a
        result dict with ``isError = True`` rather than raising.
        """
        ...


# Sentinel ``ChoiceCard.id`` emitted between the seen-prefix and the new-suffix
# when ``ProxyRuntime(cache_stable=True)`` reorders a browse response. The
# marker is a real :class:`ChoiceCard` (``kind="internal"``) so it survives any
# downstream serialisation that expects the same shape for every entry. The id
# starts with a double underscore so it cannot collide with any canonical
# ``tool_id`` per ``docs/gateway_spec.md`` §1.1 (which uses ``:`` separators
# and a stricter character set).
CACHE_BREAKPOINT_ID: str = "__cache_breakpoint__"

# Meta-tool names the per-session rate limiter keys quotas on (mirrors
# ``gateway_policy.META_TOOL_NAMES`` / ``mcp_gateway.GATEWAY_TOOL_NAMES``).
# Defined here as literals to avoid importing ``mcp_gateway`` (which imports
# this module).
_TOOL_BROWSE = "tool_browse"
_TOOL_EXECUTE = "tool_execute"
_TOOL_VIEW = "tool_view"

# A single-attempt policy used when no ``retry_policy`` is configured, so the
# execute path always flows through ``call_with_retry`` (one attempt = today's
# behaviour) and keeps a single upstream-error-to-``GatewayError`` mapping.
_SINGLE_ATTEMPT = RetryPolicy()


class ProxyRuntime:
    """Shared core for the MCP proxy and gateway modes.

    Args:
        upstream: An :class:`UpstreamCall` implementation.
        mode: Which agent-facing surface this runtime serves.  Defaults
            to :attr:`ExposureMode.GATEWAY`.
        context_manager: Optional pre-built
            :class:`~contextweaver.context.manager.ContextManager`.
            Defaults to a fresh one with the standard 4-store bundle.
        tree_builder: Optional custom
            :class:`~contextweaver.routing.tree.TreeBuilder`.
        beam_width: Router beam width passed through to
            :class:`~contextweaver.routing.router.Router`.
        top_k: Maximum number of cards returned by :meth:`browse`.
        cache_stable: When ``True``, :meth:`browse` reorders the returned
            cards so previously-browsed-or-hydrated tools appear first in
            ascending-``id`` order, followed by a :data:`CACHE_BREAKPOINT_ID`
            marker card, followed by newly-discovered tools (also
            ascending-``id`` order). This produces a byte-stable prompt
            prefix across repeated browses in the same session — see
            ``docs/gateway_spec.md`` §5 (cache-stable browse) and the
            Webfuse MCP cheat sheet pattern referenced from
            ``docs/integration_mcp.md``. Default ``False`` preserves the
            §2.5 score-desc / id-asc ordering. Ranking metadata is
            preserved on each :class:`ChoiceCard` via ``score`` so
            downstream consumers can still rank after the fact —
            **the first emitted card is not guaranteed to be the
            highest-ranked card when this flag is on**.
        diagnostic_sink: Optional destination for sanitized catalog, browse,
            hydrate, execute, and artifact-view events.
        session_id: Optional stable identifier attached to diagnostic events.
            A random identifier is generated when omitted.
        on_invalid: How to handle malformed upstream tool definitions and
            schemas at catalog ingest (issues #464 / #484).  ``"skip"`` (default)
            drops the offending tool and records it on
            :attr:`last_refresh_report`; ``"raise"`` re-raises so development
            catalogs fail loudly.
        schema_limits: Complexity bounds applied to untrusted tool schemas at
            ingest (issue #484).  Defaults to ``DEFAULT_SCHEMA_LIMITS``.
        tolerant_args: When ``True``, run a deterministic, opt-in argument
            repair pass (issue #488) before strict validation in
            :meth:`execute`.  Off by default — behaviour is then byte-identical
            to strict validation.
        retry_policy: Opt-in bounded-backoff retry for transient upstream
            failures in :meth:`execute` (issue #529).  ``None`` (default) keeps
            today's single-attempt behaviour.
        rate_limiter: Opt-in per-session invocation quotas on the meta-tools
            (issue #482).  ``None`` (default) applies no limits.
        result_cache: Opt-in response cache for read-only tools (issue #512).
            ``None`` (default) disables caching; results are never cached unless
            this is set *and* the tool is upstream-declared read-only.
        retry_sleep: Awaitable sleep used for retry backoff; injected in tests.
            Defaults to :func:`asyncio.sleep`.
        jitter_source: Optional ``() -> float in [0, 1)`` supplying the jitter
            fraction per backoff delay.  Omitted ⇒ deterministic schedule.
    """

    def __init__(
        self,
        upstream: UpstreamCall,
        *,
        mode: ExposureMode = ExposureMode.GATEWAY,
        context_manager: ContextManager | None = None,
        tree_builder: TreeBuilder | None = None,
        beam_width: int = 3,
        top_k: int = 10,
        cache_stable: bool = False,
        diagnostic_sink: DiagnosticSink | None = None,
        session_id: str | None = None,
        redact_secrets: bool = False,
        policy: ToolPolicy | None = None,
        on_invalid: Literal["skip", "raise"] = "skip",
        schema_limits: SchemaLimits | None = None,
        tolerant_args: bool = False,
        retry_policy: RetryPolicy | None = None,
        rate_limiter: RateLimiter | None = None,
        result_cache: ToolResultCache | None = None,
        retry_sleep: Sleeper | None = None,
        jitter_source: Callable[[], float] | None = None,
    ) -> None:
        self._upstream = upstream
        self._mode = mode
        # Issue #428 — when secret redaction is requested and no manager is
        # supplied, the default manager inherits it so the gateway's firewalled
        # tool results are scrubbed alongside its ChoiceCards.  A *caller-supplied*
        # manager is never mutated; if it was not itself built with
        # ``redact_secrets=True`` the gateway still scrubs ChoiceCards but the
        # firewall summaries are not scrubbed — warn so the partial coverage is
        # explicit rather than silent.
        if context_manager is not None and redact_secrets and not context_manager._redact_secrets:
            logger.warning(
                "ProxyRuntime(redact_secrets=True) with a caller-supplied "
                "context_manager that has redact_secrets=False: ChoiceCard text "
                "will be scrubbed but firewall summaries will not. Construct the "
                "manager with ContextManager(redact_secrets=True) for end-to-end "
                "scrubbing."
            )
        self._context_manager = context_manager or ContextManager(redact_secrets=redact_secrets)
        self._tree_builder = tree_builder or TreeBuilder()
        self._beam_width = beam_width
        self._top_k = top_k
        self._cache_stable = cache_stable
        #: When ``True`` the gateway scrubs secret shapes from ChoiceCard text
        #: (name/description/tags) before they reach the prompt (issue #428).
        self._redact_secrets = redact_secrets
        #: Runtime authorization gate for ``tool_execute`` / ``tool_view``
        #: (issues #373 / #746). ``None`` (default) allows every call, as before.
        self._policy = policy
        self._browsed_tool_ids: set[str] = set()
        # First-sighting frozen card content keyed by ``tool_id``. Used by
        # ``_maybe_cache_stable`` so the byte-stable prefix really is
        # byte-stable: subsequent browses with different queries produce
        # different scores for the same item, which would otherwise drift
        # the serialised prefix. The cache freezes that content the first
        # time a card is emitted.
        self._cached_cards: dict[str, ChoiceCard] = {}
        self._catalog: Catalog = Catalog()
        self._graph: ChoiceGraph | None = None
        self._router: Router | None = None
        self._upstream_names = UpstreamNameIndex()
        self._raw_tool_defs: dict[str, dict[str, Any]] = {}
        self._telemetry = GatewayTelemetry(diagnostic_sink, session_id=session_id)
        #: Ingest-time hardening configuration (issues #464 / #484 / #488).
        self._on_invalid = on_invalid
        self._schema_limits = schema_limits or DEFAULT_SCHEMA_LIMITS
        self._tolerant_args = tolerant_args
        #: Opt-in dispatch-path controls (issues #529 / #482 / #512). All inert
        #: by default — an unconfigured runtime behaves exactly as before.
        self._retry_policy = retry_policy
        self._rate_limiter = rate_limiter
        self._result_cache = result_cache
        self._retry_sleep: Sleeper = retry_sleep or asyncio.sleep
        self._jitter_source = jitter_source
        #: Compiled ``jsonschema`` validators keyed by ``tool_id`` so the hot
        #: ``execute`` path validates without recompiling (issue #484). Cleared
        #: on every catalog refresh.
        self._validator_cache: dict[str, SchemaValidator] = {}
        #: Outcome of the most recent catalog refresh (issues #464 / #484).
        self._last_refresh_report = CatalogRefreshReport()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def mode(self) -> ExposureMode:
        """Return the configured :class:`ExposureMode`."""
        return self._mode

    @property
    def catalog(self) -> Catalog:
        """Return the active :class:`Catalog`."""
        return self._catalog

    @property
    def context_manager(self) -> ContextManager:
        """Return the per-session :class:`ContextManager`."""
        return self._context_manager

    @property
    def cache_stable(self) -> bool:
        """Whether browse responses are reordered for a byte-stable prefix."""
        return self._cache_stable

    @property
    def browsed_tool_ids(self) -> frozenset[str]:
        """Snapshot of every ``tool_id`` that has been browsed or hydrated.

        Only populated when :attr:`cache_stable` is ``True``. Returned as a
        :class:`frozenset` so callers cannot mutate runtime state through it.
        """
        return frozenset(self._browsed_tool_ids)

    @property
    def diagnostic_session_id(self) -> str:
        """Return the identifier attached to this runtime's diagnostic events."""
        return self._telemetry.session_id

    # ------------------------------------------------------------------
    # Catalog management
    # ------------------------------------------------------------------

    async def refresh_catalog(self) -> int:
        """Re-fetch the upstream ``tools/list`` and rebuild the catalog.

        Returns:
            The number of tools registered.
        """
        tool_defs = await self._upstream.list_tools()
        return self._register_tool_defs(tool_defs)

    def register_tool_defs_sync(self, tool_defs: list[dict[str, Any]]) -> int:
        """Register *tool_defs* (MCP-format) into the catalog synchronously.

        Useful for tests and demos that want to bypass the async upstream
        boundary.  Returns the number of registered tools.
        """
        return self._register_tool_defs(tool_defs)

    def _register_tool_defs(self, tool_defs: list[dict[str, Any]]) -> int:
        # Invalidate cache-stable state and compiled validators: tool
        # definitions (and therefore schemas) may have changed.
        self._cached_cards.clear()
        self._browsed_tool_ids.clear()
        self._validator_cache.clear()
        # Cached upstream responses may be stale once the catalog changes — a
        # refreshed tool_id can resolve to a different upstream schema (#512 /
        # #507). All derived state below is rebuilt within this single
        # synchronous call, so executions never observe a half-updated view.
        if self._result_cache is not None:
            self._result_cache.invalidate_all()
        report = CatalogRefreshReport()
        items: list[SelectableItem] = []
        upstream_index: dict[str, str] = {}
        raw_defs: dict[str, dict[str, Any]] = {}
        for index, tool_def in enumerate(tool_defs):
            item = self._convert_tool_def(index, tool_def, report)
            if item is None:
                continue
            items.append(item)
            upstream_index[item.id] = str(tool_def["name"])
            raw_defs[item.id] = dict(tool_def)
        self._catalog = Catalog()
        for item in items:
            self._catalog.register(item)
        self._upstream_names = UpstreamNameIndex(by_tool_id=upstream_index)
        self._raw_tool_defs = raw_defs
        if items:
            self._graph = self._tree_builder.build(items)
            self._router = Router(
                self._graph,
                items=items,
                beam_width=self._beam_width,
                top_k=self._top_k,
            )
        else:
            self._graph = None
            self._router = None
        report.registered = len(items)
        self._last_refresh_report = report
        self._telemetry.catalog_registered(items, raw_defs, mode=self._mode.value)
        logger.debug(
            "proxy_runtime: registered %d tools (%d skipped, %d schema findings)",
            len(items),
            len(report.skipped),
            len(report.schema_findings),
        )
        return len(items)

    def _convert_tool_def(
        self,
        index: int,
        tool_def: object,
        report: CatalogRefreshReport,
    ) -> SelectableItem | None:
        """Convert one upstream tool def defensively (issues #464 / #484).

        Returns the :class:`SelectableItem`, or ``None`` when the definition is
        malformed and ``on_invalid="skip"`` (the skip is recorded on *report*).
        Raises in ``on_invalid="raise"`` mode.
        """
        name_repr = ""
        try:
            if not isinstance(tool_def, dict):
                raise CatalogError(f"tool definition is not a dict: {type(tool_def).__name__}")
            raw_name = tool_def.get("name")
            name_repr = str(raw_name) if isinstance(raw_name, str) else ""
            if not isinstance(raw_name, str) or not raw_name.strip():
                raise CatalogError("tool definition has no non-empty string 'name'")
            item = mcp_tool_to_selectable(tool_def)
        except (CatalogError, KeyError, TypeError, ValueError) as exc:
            if self._on_invalid == "raise":
                raise
            report.skipped.append(
                SkippedTool(index=index, name=name_repr, reason=redact_upstream_detail(str(exc)))
            )
            # Use %r for upstream-controlled values (name, exception text): repr
            # escapes any control characters, preventing terminal-escape /
            # log-injection via a hostile tool definition (#464).
            logger.warning(
                "proxy_runtime: skipping malformed tool def at index %d (%r): %r",
                index,
                name_repr or "<no name>",
                exc,
            )
            return None

        findings = check_schema_health(item.id, item.args_schema, limits=self._schema_limits)
        if item.output_schema:
            findings += check_schema_health(item.id, item.output_schema, limits=self._schema_limits)
        if findings:
            if self._on_invalid == "raise":
                first = findings[0]
                raise CatalogError(
                    f"tool {item.id} has an invalid schema ({first.kind}): {first.detail}"
                )
            report.schema_findings.extend(findings)
            for finding in findings:
                # detail can embed untrusted schema text — repr it (#464/#484).
                logger.warning(
                    "proxy_runtime: schema finding for %s: %s (%r)",
                    finding.tool_id,
                    finding.kind,
                    finding.detail,
                )
            # Lenient mode flags and continues: the tool is still registered so
            # one bad schema does not erase a usable catalog (#484).
        return item

    @property
    def last_refresh_report(self) -> CatalogRefreshReport:
        """Outcome of the most recent catalog refresh (issues #464 / #484).

        Records every malformed tool definition skipped and every schema-health
        finding raised during the last :meth:`refresh_catalog` /
        :meth:`register_tool_defs_sync` call, so operators can audit catalog
        quality without parsing log lines.
        """
        return self._last_refresh_report

    def list_tool_ids(self) -> list[str]:
        """Return the canonical ``tool_id`` for every registered tool."""
        return [item.id for item in self._catalog.all()]

    # ------------------------------------------------------------------
    # tool_browse (§3 + §4.2)
    # ------------------------------------------------------------------

    def browse(
        self,
        *,
        query: str | None = None,
        path: str | None = None,
        top_k: int | None = None,
    ) -> list[ChoiceCard] | GatewayError:
        """Implement ``tool_browse(query|path)`` per §3.1.

        Exactly one of *query* or *path* must be supplied — passing both
        or neither returns :class:`GatewayError` with code
        ``ARGS_INVALID``.

        Args:
            query: Free-form natural-language query.  Routed through
                :class:`Router`.
            path: Hierarchical path through the :class:`ChoiceGraph`.
            top_k: Optional override for the configured top-k count.

        Returns:
            A list of :class:`ChoiceCard` bounded per §2.3, or a
            :class:`GatewayError` describing why the request was
            rejected.
        """
        started = perf_counter()
        if self._rate_limiter is not None:
            decision = self._rate_limiter.check(_TOOL_BROWSE)
            if not decision.allowed:
                limited = rate_limited_error("", decision)
                self._telemetry.browse_completed(
                    limited,
                    duration_ms=(perf_counter() - started) * 1000,
                    query_chars=len(query) if query is not None else 0,
                    path_depth=len([part for part in (path or "").split("/") if part]),
                    raw_defs=self._raw_tool_defs,
                )
                return limited
        if (query is None) == (path is None):
            result: list[ChoiceCard] | GatewayError = GatewayError(
                code="ARGS_INVALID",
                message="tool_browse requires exactly one of 'query' or 'path'.",
            )
        elif query is not None:
            result = self._browse_by_query(query, top_k=top_k)
        else:
            assert path is not None  # narrowed by the XOR check above
            result = self._browse_by_path(path)
        self._telemetry.browse_completed(
            result,
            duration_ms=(perf_counter() - started) * 1000,
            query_chars=len(query) if query is not None else 0,
            path_depth=len([part for part in (path or "").split("/") if part]),
            raw_defs=self._raw_tool_defs,
        )
        return result

    def _browse_by_query(self, query: str, *, top_k: int | None) -> list[ChoiceCard] | GatewayError:
        if self._router is None or not self._catalog.all():
            return []
        effective_top_k = top_k if top_k is not None else self._top_k
        # Router.top_k is set at construction to self._top_k; per-call
        # overrides are applied by truncating the result via make_choice_cards.
        # Values larger than self._top_k are silently capped at self._top_k.
        result = self._router.route(query)
        scores = dict(zip(result.candidate_ids, result.scores, strict=False))
        cards = make_choice_cards(
            result.candidate_items,
            max_cards=effective_top_k,
            scores=scores,
            redact_secrets=self._redact_secrets,
        )
        return self._maybe_cache_stable(bound_browse_response(cards))

    def _browse_by_path(self, path: str) -> list[ChoiceCard] | GatewayError:
        if self._graph is None:
            return GatewayError(
                code="PATH_NOT_FOUND",
                message="No catalog registered.",
                path=path,
            )
        try:
            segments = parse_path(path)
        except PathInvalidError as exc:
            return GatewayError(code="PATH_INVALID", message=str(exc), path=path)
        try:
            child_ids = resolve_path(self._graph, segments)
        except PathInvalidError as exc:
            return GatewayError(code="PATH_INVALID", message=str(exc), path=path)
        except PathNotFoundError as exc:
            return GatewayError(code="PATH_NOT_FOUND", message=str(exc), path=path)
        cards: list[ChoiceCard] = []
        for child_id in child_ids:
            try:
                cards.append(
                    item_to_card(self._catalog.get(child_id), redact_secrets=self._redact_secrets)
                )
            except ItemNotFoundError:
                # Navigation node, not a leaf — synthesise a cluster card.
                node = self._graph.get_node(child_id)
                cards.append(
                    ChoiceCard(
                        id=child_id,
                        name=node.label or child_id,
                        description=node.routing_hint or "Cluster",
                        kind="internal",
                        namespace=child_id.split(":", 1)[0] if ":" in child_id else "",
                    )
                )
        return self._maybe_cache_stable(bound_browse_response(cards))

    # ------------------------------------------------------------------
    # tool_hydrate (§4.1)
    # ------------------------------------------------------------------

    def hydrate(self, tool_id: str) -> HydrationResult | GatewayError:
        """Return the full schema for *tool_id* (§4.3).

        When :attr:`cache_stable` is ``True``, a successful hydration is
        recorded so subsequent :meth:`browse` calls will surface *tool_id*
        in the byte-stable prefix.

        Returns:
            A :class:`HydrationResult` or :class:`GatewayError` with code
            ``HYDRATE_FAILED`` when *tool_id* is unknown.
        """
        started = perf_counter()
        namespace: str | None = None
        result: HydrationResult | GatewayError
        try:
            result = self._catalog.hydrate(tool_id)
            namespace = result.item.namespace
        except ItemNotFoundError as exc:
            result = GatewayError(
                code="HYDRATE_FAILED",
                message=str(exc),
                path=tool_id,
            )
        if self._cache_stable and not isinstance(result, GatewayError):
            self._browsed_tool_ids.add(tool_id)
        self._telemetry.hydrate_completed(
            tool_id,
            result,
            duration_ms=(perf_counter() - started) * 1000,
            namespace=namespace,
        )
        return result

    # ------------------------------------------------------------------
    # cache-stable browse helper (§5)
    # ------------------------------------------------------------------

    def _maybe_cache_stable(
        self, cards: list[ChoiceCard] | GatewayError
    ) -> list[ChoiceCard] | GatewayError:
        """Pass *cards* through the cache-stable reordering if enabled.

        No-op when :attr:`cache_stable` is ``False`` or when *cards* is a
        :class:`GatewayError`. When enabled: split *cards* into
        previously-seen vs newly-discovered, sort each half by ``id``
        ascending, insert a :data:`CACHE_BREAKPOINT_ID` marker if both
        halves are non-empty, and record the union back into the
        session's browsed-id set.

        ``ChoiceCard.score`` is preserved on every card so downstream
        consumers can re-rank after the fact — the marker carries no
        score (``None``) and is the explicit boundary between the
        cache-stable prefix and the score-rankable suffix.
        """
        if not self._cache_stable or isinstance(cards, GatewayError):
            return cards
        # Snapshot the seen-set BEFORE this call so the partition is
        # well-defined regardless of what we add below.
        previously_seen = frozenset(self._browsed_tool_ids)
        # Freeze first-sighting card content. The cache then guarantees that
        # subsequent browses with different queries still emit byte-identical
        # bytes for ids in the prefix, even though the router would have
        # produced different ``score`` values for them on this call.
        for card in cards:
            self._cached_cards.setdefault(card.id, card)
        seen_cards = sorted(
            (self._cached_cards[c.id] for c in cards if c.id in previously_seen),
            key=lambda c: c.id,
        )
        new_cards = sorted(
            (c for c in cards if c.id not in previously_seen),
            key=lambda c: c.id,
        )
        self._browsed_tool_ids.update(c.id for c in cards)
        if seen_cards and new_cards:
            # Reserve one slot for the marker so total never exceeds top_k.
            max_new = max(0, self._top_k - len(seen_cards) - 1)
            new_cards = new_cards[:max_new]
            if not new_cards:
                return seen_cards
            marker = ChoiceCard(
                id=CACHE_BREAKPOINT_ID,
                name="cache_breakpoint",
                description=(
                    "Cache-stable prefix above; newly-discovered tools below "
                    "(read ChoiceCard.score for rank)."
                ),
                kind="internal",
            )
            return [*seen_cards, marker, *new_cards]
        # Defensive: ensure total never exceeds top_k even without marker.
        combined = [*seen_cards, *new_cards]
        return combined[: self._top_k]

    # ------------------------------------------------------------------
    # tool_execute (§4.2 + §4.4)
    # ------------------------------------------------------------------

    async def execute(
        self,
        tool_id: str,
        args: dict[str, Any],
        *,
        dry_run: bool = False,
    ) -> ResultEnvelope | GatewayError | DryRunReport:
        """Validate *args*, invoke upstream, and return a compacted envelope.

        The dispatch path applies the configured controls in order: per-session
        quota check (issue #482) → dry-run short-circuit (issue #483) →
        read-only response-cache lookup (issue #512) → upstream dispatch with
        bounded retry (issue #529) → cache store.  All are inert unless
        configured, so an unconfigured runtime behaves exactly as before.

        Args:
            tool_id: Canonical ``tool_id`` of the target tool.
            args: Arguments to pass through to the upstream MCP server.
            dry_run: When ``True``, run every pre-dispatch step (hydration,
                validation, quota evaluation) and return a :class:`DryRunReport`
                **without** invoking upstream or writing artifacts (issue #483).

        Returns:
            A :class:`ResultEnvelope` (post-firewall), a :class:`GatewayError`,
            or — when *dry_run* is set and the call is valid — a
            :class:`DryRunReport`.  Validation failures map to ``ARGS_INVALID``
            per §4.4 (identically for dry runs); transport / protocol failures
            map to the §4.4 upstream taxonomy; a quota breach maps to
            ``RATE_LIMITED``.
        """
        started = perf_counter()
        arg_keys = sorted(args)
        namespace: str | None = None
        try:
            hydrated = self._catalog.hydrate(tool_id)
            namespace = hydrated.item.namespace
        except ItemNotFoundError as exc:
            error = GatewayError(
                code="HYDRATE_FAILED",
                message=str(exc),
                path=tool_id,
            )
            self._telemetry.execute_failed(
                tool_id,
                error,
                duration_ms=(perf_counter() - started) * 1000,
                namespace=namespace,
                arg_keys=arg_keys,
            )
            return error
        schema = hydrated.args_schema
        repairs: list[Repair] = []
        if self._tolerant_args and schema:
            args, repairs = normalize_args(args, schema)
            arg_keys = sorted(args) if isinstance(args, dict) else arg_keys
        validation_error = self._validate_args(tool_id, args, schema)
        if validation_error is not None:
            self._telemetry.execute_failed(
                tool_id,
                validation_error,
                duration_ms=(perf_counter() - started) * 1000,
                namespace=namespace,
                arg_keys=arg_keys,
            )
            return validation_error
        upstream_name = self._upstream_names.by_tool_id.get(tool_id, hydrated.item.name)
        read_only = not hydrated.item.side_effects

        # Runtime authorization gate (issue #373): decide allow/deny/approval
        # after schema validation and *before* any upstream dispatch. The verdict
        # is computed for dry runs too, so a dry run reflects real authorization
        # (surfaced as a "policy" check below); only a real call is blocked here,
        # and a denied/approval-required tool is never dispatched upstream.
        policy_error: GatewayError | None = None
        if self._policy is not None:
            policy_error = execute_policy_error(
                self._policy,
                hydrated.item,
                tool_id=tool_id,
                upstream_name=upstream_name,
                args=args,
                read_only=read_only,
                raw_def=self._raw_tool_defs.get(tool_id, {}),
                exposure_mode=self._mode.value,
            )
            if not dry_run and policy_error is not None:
                self._telemetry.execute_failed(
                    tool_id,
                    policy_error,
                    duration_ms=(perf_counter() - started) * 1000,
                    namespace=namespace,
                    arg_keys=arg_keys,
                )
                return policy_error

        # Per-session quota (issue #482). Dry runs evaluate the limit for the
        # report but never consume quota.
        rate_decision = RateLimitDecision(allowed=True)
        if self._rate_limiter is not None:
            rate_decision = self._rate_limiter.check(
                _TOOL_EXECUTE, tool_id=tool_id, record=not dry_run
            )
        if not dry_run and not rate_decision.allowed:
            error = rate_limited_error(tool_id, rate_decision)
            self._telemetry.execute_failed(
                tool_id,
                error,
                duration_ms=(perf_counter() - started) * 1000,
                namespace=namespace,
                arg_keys=arg_keys,
            )
            return error

        # Dry run (issue #483): every pre-dispatch check ran above; report the
        # would-be call without dispatching upstream or writing artifacts.
        if dry_run:
            report = build_dry_run_report(
                tool_id,
                upstream_name,
                self._raw_tool_defs.get(tool_id, {}),
                rate_allowed=rate_decision.allowed,
                policy_status=(
                    None
                    if self._policy is None
                    else ("pass" if policy_error is None else policy_error.code)
                ),
            )
            self._telemetry.execute_dry_run(
                tool_id,
                duration_ms=(perf_counter() - started) * 1000,
                namespace=namespace,
                arg_keys=arg_keys,
            )
            return report

        # Opt-in read-only response cache (issue #512). Only read-only tools the
        # operator admitted are eligible; non-serialisable args skip caching.
        cache_key: str | None = None
        if self._result_cache is not None and read_only and self._result_cache.admits(tool_id):
            cache_key = self._result_cache.key(tool_id, args)
            if cache_key is not None:
                cached = self._result_cache.get(cache_key)
                if cached is not None:
                    cached.provenance["cache_hit"] = True
                    self._telemetry.execute_cache_hit(
                        tool_id,
                        cached,
                        duration_ms=(perf_counter() - started) * 1000,
                        namespace=namespace,
                        arg_keys=arg_keys,
                    )
                    return cached

        # Upstream dispatch with bounded retry (issue #529). The default
        # single-attempt policy makes this one call — identical to before.
        outcome = await call_with_retry(
            lambda: self._upstream.call_tool(upstream_name, args),
            policy=self._retry_policy or _SINGLE_ATTEMPT,
            classify=classify_upstream_exception,
            sleep=self._retry_sleep,
            jitter_source=self._jitter_source,
        )
        if outcome.error is not None:
            failure = outcome.error
            code, retryable = classify_upstream_exception(failure)
            # Full, unredacted detail goes to the operator-side log only; the
            # model-visible message is classified, length-capped, and stripped
            # of control characters (issue #485).
            logger.warning(
                "proxy_runtime: upstream call for %s failed [%s] after %d attempt(s): %r",
                tool_id,
                code,
                outcome.attempts,
                failure,
            )
            error = GatewayError(
                code=code,
                message=f"upstream call failed: {redact_upstream_detail(str(failure))}",
                path=tool_id,
                retryable=retryable,
            )
            self._telemetry.execute_failed(
                tool_id,
                error,
                duration_ms=(perf_counter() - started) * 1000,
                namespace=namespace,
                arg_keys=arg_keys,
            )
            return error
        raw = outcome.raw
        assert raw is not None  # narrow: outcome.error is None ⇒ raw is populated
        envelope, binaries, full_text = mcp_result_to_envelope(raw, upstream_name)
        # Persist binaries + oversized text so subsequent tool_view calls can
        # drill in (#34); helper keeps this module within its size ceiling.
        persist_result_artifacts(
            self._context_manager.artifact_store, envelope, binaries, full_text, tool_id
        )
        envelope.provenance.setdefault("tool_id", tool_id)
        if repairs:
            # Surface every applied normalization so the behaviour is auditable
            # in the result metadata and downstream traces (issue #488).
            envelope.provenance["arg_repairs"] = [repair.to_dict() for repair in repairs]
        if self._cache_stable:
            self._browsed_tool_ids.add(tool_id)
        self._telemetry.execute_completed(
            tool_id,
            envelope,
            duration_ms=(perf_counter() - started) * 1000,
            namespace=namespace,
            arg_keys=arg_keys,
            full_text=full_text,
            binary_bytes=sum(len(data) for data, _mime, _label in binaries.values()),
            attempts=outcome.attempts,
        )
        # Store after firewall stats are stamped so a later cache hit carries the
        # full envelope. Errors are never cached (issue #512).
        if cache_key is not None and self._result_cache is not None and envelope.status != "error":
            self._result_cache.put(cache_key, envelope)
        return envelope

    # ------------------------------------------------------------------
    # tool_view (#34)
    # ------------------------------------------------------------------

    def view(self, handle: str, selector: dict[str, Any]) -> str | GatewayError:
        """Drill into a previously stored artifact (#34).

        Returns the sliced text content, or a :class:`GatewayError`: ``VIEW_FAILED``
        for an unknown handle/invalid selector, or ``POLICY_DENIED`` /
        ``AUTH_REQUIRED`` when the runtime :class:`ToolPolicy` forbids raw egress
        for this handle — ``tool_view`` is the intentional raw-recovery surface and
        is governed by the same policy as ``tool_execute`` (issue #746; see
        ``docs/security_model.md``).
        """
        started = perf_counter()
        if self._policy is not None:
            policy_error = view_policy_error(
                self._policy, handle, selector, exposure_mode=self._mode.value
            )
            if policy_error is not None:
                self._telemetry.view_completed(
                    handle,
                    str(selector.get("type", "")),
                    policy_error,
                    duration_ms=(perf_counter() - started) * 1000,
                )
                return policy_error
        if self._rate_limiter is not None:
            decision = self._rate_limiter.check(_TOOL_VIEW)
            if not decision.allowed:
                limited = rate_limited_error(handle, decision)
                self._telemetry.view_completed(
                    handle,
                    str(selector.get("type", "")),
                    limited,
                    duration_ms=(perf_counter() - started) * 1000,
                )
                return limited
        # ``drilldown`` is part of the ``ArtifactStore`` protocol (#472), so the
        # gateway no longer needs to assume a concrete ``InMemoryArtifactStore``
        # backend — any conformant store (e.g. ``JsonFileArtifactStore``) works.
        store: ArtifactStore = self._context_manager.artifact_store
        try:
            result: str | GatewayError = store.drilldown(handle, selector)
        except (ArtifactNotFoundError, ContextWeaverError) as exc:
            result = GatewayError(
                code="VIEW_FAILED",
                message=str(exc),
                path=handle,
            )
        self._telemetry.view_completed(
            handle,
            str(selector.get("type", "")),
            result,
            duration_ms=(perf_counter() - started) * 1000,
        )
        return result

    # ------------------------------------------------------------------
    # Proxy-only: stripped tools/list (§4.1)
    # ------------------------------------------------------------------

    def strip_tools_list(self) -> list[dict[str, Any]]:
        """Return the stripped ``tools/list`` (§4.1) for transparent-proxy mode.

        Each entry mirrors the upstream tool's *name* and *description*
        (description truncated to the §2.4 budget) but replaces
        ``inputSchema`` with the sentinel ``{"type": "object"}``.  No
        banned fields are emitted (§2.2).

        Returns:
            A list of MCP-format tool definitions ready for the proxy's
            ``tools/list`` response.
        """
        out: list[dict[str, Any]] = []
        for item in self._catalog.all():
            card = item_to_card(item, redact_secrets=self._redact_secrets)
            out.append(
                {
                    "name": item.id,
                    "description": card.description,
                    "inputSchema": {"type": "object"},
                }
            )
        return out

    def _validate_args(
        self,
        tool_id: str,
        args: object,
        schema: dict[str, Any],
    ) -> GatewayError | None:
        """Validate *args* against *tool_id*'s schema; ``None`` on success.

        Compiles the schema's validator once and caches it by ``tool_id`` so the
        hot execute path skips recompilation (issue #484).  An upstream schema
        that fails meta-validation maps to ``SCHEMA_INVALID`` (#484); an
        argument that fails the schema maps to ``ARGS_INVALID`` (§4.4).
        """
        if not schema:
            return None
        validator = self._validator_cache.get(tool_id)
        if validator is None:
            try:
                validator = build_validator(schema)
            except jsonschema.exceptions.SchemaError as exc:
                return GatewayError(
                    code="SCHEMA_INVALID",
                    message=redact_upstream_detail(str(exc)),
                    path=tool_id,
                )
            self._validator_cache[tool_id] = validator
        try:
            validator.validate(args)
        except jsonschema.exceptions.ValidationError as exc:
            return GatewayError(
                code="ARGS_INVALID",
                message=exc.message,
                path=tool_id,
                details={"path": list(exc.path)},
            )
        return None
