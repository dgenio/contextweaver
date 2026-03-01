"""Routing sub-package for contextweaver.

Exports the catalog, graph, labeler, tree builder, router, and card renderer.
"""

from contextweaver.routing.cards import cards_for_route, format_card_for_prompt, render_cards
from contextweaver.routing.catalog import Catalog
from contextweaver.routing.graph import ChoiceGraph
from contextweaver.routing.labeler import KeywordLabeler
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder

__all__ = [
    "Catalog",
    "ChoiceGraph",
    "KeywordLabeler",
    "Router",
    "TreeBuilder",
    "cards_for_route",
    "format_card_for_prompt",
    "render_cards",
]
