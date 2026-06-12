"""Route-integrated and call-phase build methods for :class:`ContextManager`.

Extracted from :mod:`contextweaver.context.manager` (issue #101) so the
manager stays within the project's <=300 lines per module guideline (see
AGENTS.md).  :class:`_RoutingMixin` is a *partial class* of
:class:`~contextweaver.context.manager.ContextManager` — every method takes a
fully-constructed ``ContextManager`` as ``self``.  It is not a standalone base
class and is not part of the public API; ``ContextManager`` mixes it in so the
public method surface is unchanged.  The heavy assembly logic lives in
:mod:`contextweaver.context.route_build` and
:mod:`contextweaver.context.call_prompt`.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from contextweaver.context import route_build as _route_build
from contextweaver.context._manager_base import _ManagerState
from contextweaver.context.call_prompt import run_call_prompt_build

if TYPE_CHECKING:
    from contextweaver.envelope import ChoiceCard, ContextPack
    from contextweaver.routing.catalog import Catalog
    from contextweaver.routing.history import RouteHistory
    from contextweaver.routing.router import Router, RouteResult


class _RoutingMixin(_ManagerState):
    """Route → build → cards and call-phase schema-injection entry points."""

    # ------------------------------------------------------------------
    # Route-integrated build
    # ------------------------------------------------------------------

    def build_route_prompt(
        self,
        goal: str,
        query: str,
        router: Router,
        budget_tokens: int | None = None,
        *,
        history: RouteHistory | None = None,
        history_from_log: bool = True,
    ) -> tuple[ContextPack, list[ChoiceCard], RouteResult]:
        """Route, build context, and assemble a prompt with choice cards.

        Runs the router to find the best tools for *query*, then builds a
        :class:`ContextPack` for the ``route`` phase with choice cards
        appended.

        Args:
            goal: High-level goal description.
            query: User query string.
            router: The :class:`Router` to use for tool routing.
            budget_tokens: Optional budget override.
            history: Optional pre-built :class:`RouteHistory` (issue #27).
                When ``None`` and *history_from_log* is ``True``, the
                manager auto-constructs one from its event log.
            history_from_log: When ``True`` (default) and *history* is
                ``None``, build a :class:`RouteHistory` from the event log
                — already-called tools are deprioritised on subsequent
                routing calls in the same session.  Set to ``False`` to
                preserve pre-#27 stateless routing.

        Returns:
            A 3-tuple ``(pack, cards, route_result)``.
        """
        return _route_build.build_route_prompt(
            self,
            goal,
            query,
            router,
            budget_tokens,
            history=history,
            history_from_log=history_from_log,
        )

    def build_route_prompt_sync(
        self,
        goal: str,
        query: str,
        router: Router,
        budget_tokens: int | None = None,
        *,
        history: RouteHistory | None = None,
        history_from_log: bool = True,
    ) -> tuple[ContextPack, list[ChoiceCard], RouteResult]:
        """Synchronous alias for :meth:`build_route_prompt`."""
        return self.build_route_prompt(
            goal,
            query,
            router,
            budget_tokens,
            history=history,
            history_from_log=history_from_log,
        )

    # ------------------------------------------------------------------
    # Call-phase prompt (schema injection)
    # ------------------------------------------------------------------

    def _build_call_prompt(
        self,
        tool_id: str,
        query: str,
        catalog: Catalog,
        schema: dict[str, Any] | None = None,
        examples: list[str] | None = None,
        constraints: dict[str, Any] | None = None,
        budget_tokens: int | None = None,
    ) -> ContextPack:
        """Build a ``Phase.call`` prompt with the tool's full schema injected.

        Hydrates the selected tool from *catalog*, delegates header assembly
        to :func:`~contextweaver.context.call_prompt.build_schema_header`,
        then runs the standard context pipeline with the call-phase budget.

        Args:
            tool_id: ID of the tool selected during routing.
            query: User query string for relevance scoring.
            catalog: The :class:`Catalog` containing *tool_id*.
            schema: Override schema dict (replaces hydrated ``args_schema``
                in the prompt; hydration still occurs for item metadata).
            examples: Override example strings (replaces hydrated examples
                in the prompt; hydration still occurs for item metadata).
            constraints: Override constraints dict (replaces hydrated
                ``constraints`` in the prompt; hydration still occurs for
                item metadata).
            budget_tokens: Override the default ``Phase.call`` budget.

        Returns:
            A :class:`~contextweaver.envelope.ContextPack` for ``Phase.call``.

        Raises:
            ItemNotFoundError: If *tool_id* is not in *catalog*.
        """
        return run_call_prompt_build(
            self,
            tool_id=tool_id,
            query=query,
            catalog=catalog,
            schema=schema,
            examples=examples,
            constraints=constraints,
            budget_tokens=budget_tokens,
        )

    async def build_call_prompt(
        self,
        tool_id: str,
        query: str,
        catalog: Catalog,
        schema: dict[str, Any] | None = None,
        examples: list[str] | None = None,
        constraints: dict[str, Any] | None = None,
        budget_tokens: int | None = None,
    ) -> ContextPack:
        """Async wrapper for :meth:`_build_call_prompt`.

        When the manager is *async-backed* (issue #495), the synchronous
        pipeline body is offloaded to a worker thread — mirroring :meth:`build`
        — so the blocking async-store I/O driven through the store bridges does
        not stall the caller's event loop.  With synchronous stores it runs
        inline (the event loop already serialises it).

        See :meth:`_build_call_prompt` for parameter documentation.
        """
        if self._async_backed:
            return await asyncio.to_thread(
                self._build_call_prompt,
                tool_id=tool_id,
                query=query,
                catalog=catalog,
                schema=schema,
                examples=examples,
                constraints=constraints,
                budget_tokens=budget_tokens,
            )
        return self._build_call_prompt(
            tool_id=tool_id,
            query=query,
            catalog=catalog,
            schema=schema,
            examples=examples,
            constraints=constraints,
            budget_tokens=budget_tokens,
        )

    def build_call_prompt_sync(
        self,
        tool_id: str,
        query: str,
        catalog: Catalog,
        schema: dict[str, Any] | None = None,
        examples: list[str] | None = None,
        constraints: dict[str, Any] | None = None,
        budget_tokens: int | None = None,
    ) -> ContextPack:
        """Synchronous alias for :meth:`_build_call_prompt`.

        See :meth:`_build_call_prompt` for parameter documentation.
        """
        return self._build_call_prompt(
            tool_id=tool_id,
            query=query,
            catalog=catalog,
            schema=schema,
            examples=examples,
            constraints=constraints,
            budget_tokens=budget_tokens,
        )
