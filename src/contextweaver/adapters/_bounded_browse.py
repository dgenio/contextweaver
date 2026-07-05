"""Shared bounded-browse core for the tool and primitive gateway runtimes (#743).

Both :class:`~contextweaver.adapters.proxy_runtime.ProxyRuntime` (tools) and
:class:`~contextweaver.adapters._primitive_index.PrimitiveIndex`
(resources/prompts) turn a ``query`` **or** ``path`` request into a bounded list
of :class:`~contextweaver.envelope.ChoiceCard`\\s via the same sequence:
``router.route → make_choice_cards`` for a query, graph navigation +
``item_to_card`` / synthesized cluster cards for a path, then
:func:`bound_browse_response`.  That logic was copy-extracted once (to keep
``gateway_primitives.py`` under the size ceiling) and then drifted: the
``redact_secrets`` scrubbing (#428) landed only on the tool copy, silently
leaving resource/prompt cards unscrubbed on the same runtime.

This module is the single implementation both runtimes delegate to, so the
``redact_secrets`` threading (and any future hardening of the card path) can
never again reach only one copy.  Runtime-specific concerns — telemetry,
rate limiting, and the cache-stable prefix — stay in the callers and wrap the
result this function returns.  Not public API.
"""

from __future__ import annotations

from contextweaver.adapters.gateway_error import GatewayError
from contextweaver.envelope import ChoiceCard
from contextweaver.exceptions import ItemNotFoundError, PathInvalidError, PathNotFoundError
from contextweaver.routing.cards import bound_browse_response, item_to_card, make_choice_cards
from contextweaver.routing.catalog import Catalog
from contextweaver.routing.graph import ChoiceGraph
from contextweaver.routing.path import parse_path, resolve_path
from contextweaver.routing.router import Router


def bounded_browse(
    *,
    router: Router | None,
    graph: ChoiceGraph | None,
    catalog: Catalog,
    query: str | None,
    path: str | None,
    top_k: int | None,
    default_top_k: int,
    redact_secrets: bool,
    surface: str = "tool_browse",
) -> list[ChoiceCard] | GatewayError:
    """Resolve one bounded browse request to cards (or a :class:`GatewayError`).

    Exactly one of *query* / *path* must be supplied.  ``redact_secrets`` is
    threaded into every card-producing call so prompt-bound card text is
    scrubbed identically on both runtimes.

    This is the *card-production* core only; per-runtime input validation
    beyond the query/path XOR (e.g. the primitive runtime's ``top_k`` type
    guard) stays in the caller, so the tool path's existing behavior is
    preserved exactly (#743).

    Args:
        router: The kind's router, or ``None`` when the catalog is empty.
        graph: The kind's :class:`ChoiceGraph`, or ``None`` when empty.
        catalog: The kind's :class:`Catalog` (leaf lookup for path browse).
        query: Free-form query to route, or ``None``.
        path: Hierarchical graph path to resolve, or ``None``.
        top_k: Per-call cap on returned cards; ``None`` uses *default_top_k*.
        default_top_k: Configured card ceiling for this runtime.
        redact_secrets: When ``True`` (#428) card text is scrubbed via
            :func:`~contextweaver.secrets.scrub_secrets`.
        surface: Meta-tool name used in the ``ARGS_INVALID`` message
            (``"tool_browse"`` / ``"resource_browse"`` / ``"prompt_browse"``).

    Returns:
        A bounded list of :class:`ChoiceCard`, or a :class:`GatewayError`.
    """
    if (query is None) == (path is None):
        return GatewayError(
            code="ARGS_INVALID",
            message=f"{surface} requires exactly one of 'query' or 'path'.",
        )
    if query is not None:
        return _browse_by_query(
            router, query, top_k=top_k, default_top_k=default_top_k, redact_secrets=redact_secrets
        )
    return _browse_by_path(graph, catalog, path or "", redact_secrets=redact_secrets)


def _browse_by_query(
    router: Router | None,
    query: str,
    *,
    top_k: int | None,
    default_top_k: int,
    redact_secrets: bool,
) -> list[ChoiceCard] | GatewayError:
    if router is None:
        return []
    result = router.route(query)
    scores = dict(zip(result.candidate_ids, result.scores, strict=False))
    cards = make_choice_cards(
        result.candidate_items,
        max_cards=top_k if top_k is not None else default_top_k,
        scores=scores,
        redact_secrets=redact_secrets,
    )
    return bound_browse_response(cards)


def _browse_by_path(
    graph: ChoiceGraph | None,
    catalog: Catalog,
    path: str,
    *,
    redact_secrets: bool,
) -> list[ChoiceCard] | GatewayError:
    if graph is None:
        return GatewayError(code="PATH_NOT_FOUND", message="No catalog registered.", path=path)
    try:
        child_ids = resolve_path(graph, parse_path(path))
    except PathInvalidError as exc:
        return GatewayError(code="PATH_INVALID", message=str(exc), path=path)
    except PathNotFoundError as exc:
        return GatewayError(code="PATH_NOT_FOUND", message=str(exc), path=path)
    cards: list[ChoiceCard] = []
    for child_id in child_ids:
        try:
            cards.append(item_to_card(catalog.get(child_id), redact_secrets=redact_secrets))
        except ItemNotFoundError:
            # Navigation node, not a leaf — synthesise a cluster card.
            node = graph.get_node(child_id)
            cards.append(
                ChoiceCard(
                    id=child_id,
                    name=node.label or child_id,
                    description=node.routing_hint or "Cluster",
                    kind="internal",
                    namespace=child_id.split(":", 1)[0] if ":" in child_id else "",
                )
            )
    return bound_browse_response(cards)
