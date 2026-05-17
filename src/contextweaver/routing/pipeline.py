"""Explicit routing pipeline composer (issue #56).

Decomposes the routing engine into four named stages — *retrieve*,
*rerank*, *navigate*, *pack* — that can be swapped, skipped, or tuned
independently.  Mirrors the 8-stage context-engine pipeline pattern.

The default pipeline produced by :meth:`RoutingPipeline.from_config`
yields **byte-identical** output to the pre-refactor monolithic
:meth:`Router.route` implementation.  This invariant is enforced by the
existing ``tests/test_router.py`` regression gate and the
``make scorecard-check`` drift gate.

Stages:

1. ``retrieve`` — :class:`~contextweaver.protocols.Retriever` fits the
   item + node corpus (idempotent via internal flag).
2. ``rerank`` — :class:`~contextweaver.protocols.Reranker` re-orders the
   shortlist.  Defaults to :class:`NoOpReranker` which leaves order
   unchanged.
3. ``navigate`` — :class:`~contextweaver.protocols.Navigator` walks the
   :class:`~contextweaver.routing.graph.ChoiceGraph` and returns scored
   ``(item_id, score, path)`` tuples.
4. ``pack`` — :class:`~contextweaver.protocols.CardPacker` renders the
   ranked items as :class:`ChoiceCard` instances within a soft token
   budget.

Privacy: the pipeline never serialises raw item bodies; only ids, scores,
and the public ``ChoiceCard`` projection cross the stage boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from contextweaver.routing.packer import DefaultCardPacker
from contextweaver.routing.registry import EngineRegistry, default_registry

if TYPE_CHECKING:
    from contextweaver.envelope import ChoiceCard
    from contextweaver.profiles import RoutingConfig
    from contextweaver.protocols import (
        CardPacker,
        NavigationResult,
        Navigator,
        Reranker,
        Retriever,
    )
    from contextweaver.routing.graph import ChoiceGraph
    from contextweaver.types import SelectableItem


@dataclass
class RoutingPipeline:
    """Composed routing pipeline: ``retrieve → rerank → navigate → pack``.

    The pipeline itself is stateless; per-call mutable state (corpus
    indexing flag, doc-id → corpus-index map) lives on the
    :class:`~contextweaver.routing.router.Router` that owns the pipeline.

    Attributes:
        retriever: First-stage retriever (TF-IDF default).
        reranker: Optional second-stage reranker (no-op default).
        navigator: Graph navigator (beam search default).
        packer: Card packer (default wraps ``make_choice_cards``).
    """

    retriever: Retriever
    reranker: Reranker | None = None
    navigator: Navigator | None = None
    packer: CardPacker = field(default_factory=DefaultCardPacker)

    @classmethod
    def from_config(
        cls,
        config: RoutingConfig | None = None,
        *,
        registry: EngineRegistry | None = None,
    ) -> RoutingPipeline:
        """Build a default pipeline from *config* (or the default registry).

        Args:
            config: Optional :class:`~contextweaver.profiles.RoutingConfig`
                whose ``beam_width`` / ``max_depth`` / ``top_k`` /
                ``confidence_gap`` populate the bundled
                :class:`~contextweaver.routing.navigator.BeamSearchNavigator`.
            registry: Engine registry to resolve the retriever (and
                reranker, if registered).  Defaults to
                :data:`~contextweaver.routing.registry.default_registry`.

        Returns:
            A :class:`RoutingPipeline` with all four stages wired.
        """
        from contextweaver.profiles import RoutingConfig as _RoutingConfig
        from contextweaver.routing.navigator import BeamSearchNavigator

        reg = registry or default_registry
        cfg = config or _RoutingConfig()
        retriever: Retriever = reg.resolve("retriever")
        # Reranker is intentionally ``None`` by default: the pre-refactor
        # :meth:`Router.route` had no rerank stage, and adding a no-op one
        # is a behaviour change for callers who introspect the pipeline.
        # Callers wanting a reranker pass one explicitly to the dataclass
        # constructor or to :meth:`from_config` via a custom registry.
        navigator: Navigator = BeamSearchNavigator(
            beam_width=cfg.beam_width,
            max_depth=cfg.max_depth,
            top_k=cfg.top_k,
            confidence_gap=cfg.confidence_gap,
        )
        return cls(
            retriever=retriever,
            reranker=None,
            navigator=navigator,
            packer=DefaultCardPacker(),
        )

    # ------------------------------------------------------------------
    # Stage-level entry points (composable; bypass the orchestrator
    # when callers need partial control)
    # ------------------------------------------------------------------

    def navigate(
        self,
        query: str,
        graph: ChoiceGraph,
        active_items: dict[str, SelectableItem],
        doc_id_to_idx: dict[str, int],
        *,
        all_item_ids: set[str] | None = None,
        debug: bool = False,
    ) -> NavigationResult:
        """Run the navigate stage in isolation (intended for tests + benchmarks)."""
        from contextweaver.routing.navigator import BeamSearchNavigator

        nav = self.navigator or BeamSearchNavigator()
        return nav.navigate(
            query,
            graph,
            active_items,
            self.retriever,
            doc_id_to_idx,
            all_item_ids=all_item_ids,
            debug=debug,
        )

    def rerank(
        self,
        query: str,
        candidates: list[tuple[str, float]],
    ) -> list[tuple[str, float]]:
        """Run the rerank stage in isolation.  ``None`` reranker is a no-op."""
        if self.reranker is None:
            return list(candidates)
        return self.reranker.rerank(query, candidates)

    def pack(
        self,
        items: list[SelectableItem],
        scores: dict[str, float],
        *,
        budget_tokens: int | None = None,
    ) -> list[ChoiceCard]:
        """Run the pack stage in isolation."""
        return self.packer.pack(items, scores, budget_tokens=budget_tokens)
