"""Single-kind routing index for the primitive gateway runtime (#669 / #670).

Private helper extracted from
:mod:`contextweaver.adapters.gateway_primitives` to keep that module within the
≤300-line convention.  :class:`PrimitiveIndex` wraps one
:class:`~contextweaver.routing.catalog.Catalog` +
:class:`~contextweaver.routing.graph.ChoiceGraph` +
:class:`~contextweaver.routing.router.Router` for a single primitive kind
(resources *or* prompts) and exposes a bounded ``browse`` over it.  Not public
API.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from contextweaver.adapters.gateway_error import GatewayError
from contextweaver.envelope import ChoiceCard
from contextweaver.exceptions import ItemNotFoundError, PathInvalidError, PathNotFoundError
from contextweaver.routing.cards import bound_browse_response, item_to_card, make_choice_cards
from contextweaver.routing.catalog import Catalog
from contextweaver.routing.graph import ChoiceGraph
from contextweaver.routing.path import parse_path, resolve_path
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import SelectableItem


@dataclass
class PrimitiveIndex:
    """A single-kind catalog + routing graph + router with bounded browse."""

    beam_width: int = 3
    top_k: int = 10
    catalog: Catalog = field(default_factory=Catalog)
    graph: ChoiceGraph | None = None
    router: Router | None = None

    def rebuild(self, items: list[SelectableItem]) -> int:
        """Rebuild the catalog/graph/router from *items*; return the count."""
        self.catalog = Catalog()
        for item in items:
            self.catalog.register(item)
        if items:
            self.graph = TreeBuilder().build(items)
            self.router = Router(
                self.graph, items=items, beam_width=self.beam_width, top_k=self.top_k
            )
        else:
            self.graph = None
            self.router = None
        return len(items)

    def browse(
        self, *, query: str | None, path: str | None, top_k: int | None
    ) -> list[ChoiceCard] | GatewayError:
        """Browse this index by *query* (routed) or *path* (graph navigation)."""
        if (query is None) == (path is None):
            return GatewayError(
                code="ARGS_INVALID",
                message="browse requires exactly one of 'query' or 'path'.",
            )
        if query is not None:
            if self.router is None:
                return []
            result = self.router.route(query)
            scores = dict(zip(result.candidate_ids, result.scores, strict=False))
            cards = make_choice_cards(
                result.candidate_items,
                max_cards=top_k if top_k is not None else self.top_k,
                scores=scores,
            )
            return bound_browse_response(cards)
        return self._browse_path(path or "")

    def _browse_path(self, path: str) -> list[ChoiceCard] | GatewayError:
        if self.graph is None:
            return GatewayError(code="PATH_NOT_FOUND", message="No catalog registered.", path=path)
        try:
            child_ids = resolve_path(self.graph, parse_path(path))
        except PathInvalidError as exc:
            return GatewayError(code="PATH_INVALID", message=str(exc), path=path)
        except PathNotFoundError as exc:
            return GatewayError(code="PATH_NOT_FOUND", message=str(exc), path=path)
        cards: list[ChoiceCard] = []
        for child_id in child_ids:
            try:
                cards.append(item_to_card(self.catalog.get(child_id)))
            except ItemNotFoundError:
                node = self.graph.get_node(child_id)
                cards.append(
                    ChoiceCard(
                        id=child_id,
                        name=node.label or child_id,
                        description=node.routing_hint or "Cluster",
                        kind="internal",
                    )
                )
        return bound_browse_response(cards)
