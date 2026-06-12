"""Core context-build methods for :class:`ContextManager`.

Extracted from :mod:`contextweaver.context.manager` (issue #101) so the
manager stays within the project's <=300 lines per module guideline (see
AGENTS.md).  :class:`_BuildMixin` is a *partial class* of
:class:`~contextweaver.context.manager.ContextManager` — every method takes a
fully-constructed ``ContextManager`` as ``self``.  It is not a standalone base
class and is not part of the public API; ``ContextManager`` mixes it in so the
public ``build`` / ``build_sync`` surface is unchanged.  The heavy pipeline
logic lives in :mod:`contextweaver.context.build`.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Literal, overload

from contextweaver.context._manager_base import _ManagerState
from contextweaver.context.build import run_build_pipeline
from contextweaver.exceptions import ContextWeaverError
from contextweaver.types import Phase

if TYPE_CHECKING:
    from contextweaver.context.explanation import ContextBuildExplanation
    from contextweaver.envelope import ContextPack


class _BuildMixin(_ManagerState):
    """Synchronous + async context-build entry points for :class:`ContextManager`."""

    def _build(
        self,
        phase: Phase = Phase.answer,
        query: str = "",
        query_tags: list[str] | None = None,
        header: str = "",
        footer: str = "",
        budget_tokens: int | None = None,
        hints: list[str] | None = None,
        extra: dict[str, Any] | None = None,
        explain: bool = False,
    ) -> tuple[ContextPack, ContextBuildExplanation | None]:
        """Run the full context compilation pipeline (synchronous core).

        All eight pipeline steps are pure computation, so no ``await`` is
        needed.  Both :meth:`build` (async) and :meth:`build_sync` delegate
        here.

        Args:
            phase: Active execution phase.
            query: User query string used for relevance scoring.
            query_tags: Optional tag list to boost tag-matched items.
            header: Optional prompt header text.
            footer: Optional prompt footer text.
            budget_tokens: Override the default phase budget.
            hints: Additional hint tags for scoring.
            extra: Reserved for future pipeline extensions.
            explain: When ``True``, collect explanation-specific
                intermediate state and return a populated
                :class:`ContextBuildExplanation`.  When ``False``
                (default), skip explanation overhead and return
                ``None`` as the second tuple element (issue #291).

        Returns:
            A 2-tuple ``(pack, explanation)``.  *explanation* is
            ``None`` when *explain* is ``False``.
        """
        return run_build_pipeline(
            self,
            phase=phase,
            query=query,
            query_tags=query_tags,
            header=header,
            footer=footer,
            budget_tokens=budget_tokens,
            hints=hints,
            extra=extra,
            explain=explain,
        )

    @overload
    async def build(
        self,
        phase: Phase = ...,
        query: str = ...,
        query_tags: list[str] | None = ...,
        header: str = ...,
        footer: str = ...,
        budget_tokens: int | None = ...,
        hints: list[str] | None = ...,
        extra: dict[str, Any] | None = ...,
        *,
        explain: Literal[False] = False,
    ) -> ContextPack: ...

    @overload
    async def build(
        self,
        phase: Phase = ...,
        query: str = ...,
        query_tags: list[str] | None = ...,
        header: str = ...,
        footer: str = ...,
        budget_tokens: int | None = ...,
        hints: list[str] | None = ...,
        extra: dict[str, Any] | None = ...,
        *,
        explain: Literal[True],
    ) -> tuple[ContextPack, ContextBuildExplanation]: ...

    async def build(
        self,
        phase: Phase = Phase.answer,
        query: str = "",
        query_tags: list[str] | None = None,
        header: str = "",
        footer: str = "",
        budget_tokens: int | None = None,
        hints: list[str] | None = None,
        extra: dict[str, Any] | None = None,
        *,
        explain: bool = False,
    ) -> ContextPack | tuple[ContextPack, ContextBuildExplanation]:
        """Asynchronously compile a :class:`~contextweaver.envelope.ContextPack`.

        The current pipeline is fully synchronous; this ``async`` wrapper
        exists so callers can ``await`` it today and benefit from true
        async I/O if the pipeline gains ``await``-able steps in the future.

        See :meth:`_build` for full parameter documentation.

        Args:
            phase: Active execution phase.
            query: User query string used for relevance scoring.
            query_tags: Optional tag list to boost tag-matched items.
            header: Optional prompt header text.
            footer: Optional prompt footer text.
            budget_tokens: Override the default phase budget.
            hints: Additional hint tags for scoring.
            extra: Reserved for future pipeline extensions.
            explain: Keyword-only.  When ``True``, the method returns a
                ``(pack, explanation)`` tuple where *explanation* is a
                :class:`ContextBuildExplanation` capturing per-candidate
                scoring + drop reasons.  Default ``False`` preserves the
                bare :class:`ContextPack` return shape (issue #291).

        Returns:
            A :class:`~contextweaver.envelope.ContextPack` ready for
            the LLM, or a ``(pack, explanation)`` tuple when
            ``explain=True``.
        """
        if self._async_backed:
            # Issue #495: the pipeline reads/writes async stores through
            # blocking async-to-sync bridges. Run the synchronous body in a
            # worker thread so those blocking waits happen off the caller's
            # event loop, which stays free to service other tasks.
            pack, explanation = await asyncio.to_thread(
                self._build,
                phase=phase,
                query=query,
                query_tags=query_tags,
                header=header,
                footer=footer,
                budget_tokens=budget_tokens,
                hints=hints,
                extra=extra,
                explain=explain,
            )
        else:
            pack, explanation = self._build(
                phase=phase,
                query=query,
                query_tags=query_tags,
                header=header,
                footer=footer,
                budget_tokens=budget_tokens,
                hints=hints,
                extra=extra,
                explain=explain,
            )
        if explain:
            if explanation is None:  # invariant: _build populates it when explain=True
                raise ContextWeaverError(
                    "internal invariant violated: explain=True but no explanation was built"
                )
            return (pack, explanation)
        return pack

    @overload
    def build_sync(
        self,
        phase: Phase = ...,
        query: str = ...,
        query_tags: list[str] | None = ...,
        header: str = ...,
        footer: str = ...,
        budget_tokens: int | None = ...,
        hints: list[str] | None = ...,
        extra: dict[str, Any] | None = ...,
        *,
        explain: Literal[False] = False,
    ) -> ContextPack: ...

    @overload
    def build_sync(
        self,
        phase: Phase = ...,
        query: str = ...,
        query_tags: list[str] | None = ...,
        header: str = ...,
        footer: str = ...,
        budget_tokens: int | None = ...,
        hints: list[str] | None = ...,
        extra: dict[str, Any] | None = ...,
        *,
        explain: Literal[True],
    ) -> tuple[ContextPack, ContextBuildExplanation]: ...

    def build_sync(
        self,
        phase: Phase = Phase.answer,
        query: str = "",
        query_tags: list[str] | None = None,
        header: str = "",
        footer: str = "",
        budget_tokens: int | None = None,
        hints: list[str] | None = None,
        extra: dict[str, Any] | None = None,
        *,
        explain: bool = False,
    ) -> ContextPack | tuple[ContextPack, ContextBuildExplanation]:
        """Synchronous entry point — delegates to :meth:`_build`.

        Works inside Jupyter notebooks, FastAPI handlers, and any other
        environment where an event loop is already running.

        See :meth:`_build` for full parameter documentation.

        Args:
            phase: Active execution phase.
            query: User query string used for relevance scoring.
            query_tags: Optional tag list to boost tag-matched items.
            header: Optional prompt header text.
            footer: Optional prompt footer text.
            budget_tokens: Override the default phase budget.
            hints: Additional hint tags for scoring.
            extra: Reserved for future pipeline extensions.
            explain: Keyword-only.  Same semantics as
                :meth:`build` (issue #291).

        Returns:
            A :class:`~contextweaver.envelope.ContextPack` ready for
            the LLM, or a ``(pack, explanation)`` tuple when
            ``explain=True``.
        """
        pack, explanation = self._build(
            phase=phase,
            query=query,
            query_tags=query_tags,
            header=header,
            footer=footer,
            budget_tokens=budget_tokens,
            hints=hints,
            extra=extra,
            explain=explain,
        )
        if explain:
            if explanation is None:  # invariant: _build populates it when explain=True
                raise ContextWeaverError(
                    "internal invariant violated: explain=True but no explanation was built"
                )
            return (pack, explanation)
        return pack
