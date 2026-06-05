"""Route-integrated context build helpers.

Extracted from :mod:`contextweaver.context.manager` (issue #101) so the
manager stays a thin orchestrator.  Holds the route → build → cards assembly
(:func:`build_route_prompt`) and the event-log-derived routing history helpers
(issue #27).  Not part of the public API — operates on a
:class:`~contextweaver.context.manager.ContextManager`'s internals.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from contextweaver.types import ItemKind, Phase

if TYPE_CHECKING:
    from contextweaver.context.manager import ContextManager
    from contextweaver.envelope import ChoiceCard, ContextPack
    from contextweaver.routing.history import RouteHistory
    from contextweaver.routing.router import Router, RouteResult
    from contextweaver.types import ContextItem


def build_route_prompt(
    manager: ContextManager,
    goal: str,
    query: str,
    router: Router,
    budget_tokens: int | None = None,
    *,
    history: RouteHistory | None = None,
    history_from_log: bool = True,
) -> tuple[ContextPack, list[ChoiceCard], RouteResult]:
    """Route, build context, and assemble a prompt with choice cards.

    See :meth:`ContextManager.build_route_prompt` for parameter documentation.
    """
    from contextweaver.routing.cards import make_choice_cards, render_cards_text

    if history is None and history_from_log:
        history = build_route_history_from_log(manager)
    route_result = (
        router.route(query, history=history) if history is not None else router.route(query)
    )

    # Build choice cards from route results
    cards = make_choice_cards(
        route_result.candidate_items,
        scores={
            cid: score
            for cid, score in zip(route_result.candidate_ids, route_result.scores, strict=False)
        },
    )

    # Render cards as text for the prompt footer
    cards_text = render_cards_text(cards)
    footer = f"[AVAILABLE TOOLS]\n{cards_text}" if cards_text else ""

    pack, _explanation = manager._build(
        phase=Phase.route,
        query=query,
        header=f"[GOAL]\n{goal}",
        footer=footer,
        budget_tokens=budget_tokens,
    )

    manager._hook.on_route_completed(route_result.candidate_ids)
    if manager._metrics is not None:
        manager._metrics.record_route(route_result)
    return pack, cards, route_result


def build_route_history_from_log(manager: ContextManager) -> RouteHistory | None:
    """Construct a :class:`RouteHistory` from the event log (issue #27).

    Returns ``None`` when the log contains no ``tool_result`` entries (the very
    first routing call in a session) so the router runs in pre-#27 stateless
    mode.  The summary is the most recent ``tool_result`` body truncated to 500
    characters; ``called_tool_ids`` is derived from each ``tool_result``'s
    originating tool call's ``function_name`` metadata via
    :func:`resolve_tool_id_from_result`.
    """
    from contextweaver.routing.history import RouteHistory

    items = manager._event_log.all()
    tool_results = [i for i in items if i.kind == ItemKind.tool_result]
    if not tool_results:
        return None
    called_ids: list[str] = []
    seen: set[str] = set()
    for item in tool_results:
        tid = resolve_tool_id_from_result(manager, item)
        if tid in seen:
            continue
        seen.add(tid)
        called_ids.append(tid)
    last = tool_results[-1]
    summary = (last.text or "")[:500] or None
    return RouteHistory(
        called_tool_ids=called_ids,
        last_result_summary=summary,
        step_number=len(called_ids) + 1,
    )


def resolve_tool_id_from_result(manager: ContextManager, item: ContextItem) -> str:
    """Derive the catalog tool id from a ``tool_result`` :class:`ContextItem`.

    Resolution order:

    1. ``item.metadata["function_name"]`` (set by the Gemini adapter).
    2. Parent ``tool_call`` item's ``metadata["function_name"]`` (OpenAI/Anthropic).
    3. Fallback to ``parent_id`` (backward-compat for simple logs).
    """
    meta = item.metadata or {}
    fn = meta.get("function_name")
    if isinstance(fn, str) and fn:
        return fn
    parent_id = item.parent_id or item.id
    try:
        parent = manager._event_log.get(parent_id)
        parent_meta = parent.metadata or {}
        parent_fn = parent_meta.get("function_name")
        if isinstance(parent_fn, str) and parent_fn:
            return parent_fn
    except Exception:  # noqa: BLE001 — ItemNotFoundError or any store issue
        pass
    return parent_id
