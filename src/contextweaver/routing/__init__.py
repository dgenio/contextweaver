"""Routing sub-package for contextweaver.

Exports the catalog, graph, labeler, tree builder, router, and card renderer.
"""

from __future__ import annotations

from contextweaver.routing.cards import (
    cards_for_route,
    format_card_for_prompt,
    make_choice_cards,
    render_cards,
    render_cards_text,
)
from contextweaver.routing.catalog import Catalog
from contextweaver.routing.graph import ChoiceGraph
from contextweaver.routing.graph_io import load_graph, save_graph
from contextweaver.routing.graph_node import ChoiceNode
from contextweaver.routing.labeler import KeywordLabeler
from contextweaver.routing.router import Router, RouteResult
from contextweaver.routing.tree import TreeBuilder

__all__ = [
    "Catalog",
    "ChoiceGraph",
    "ChoiceNode",
    "KeywordLabeler",
    "RouteResult",
    "Router",
    "TreeBuilder",
    "cards_for_route",
    "format_card_for_prompt",
    "load_graph",
    "make_choice_cards",
    "render_cards",
    "render_cards_text",
    "save_graph",
]
