"""Routing sub-package for contextweaver."""

from contextweaver.routing.cards import ChoiceCard, make_choice_cards, render_cards_text
from contextweaver.routing.catalog import (
    generate_sample_catalog,
    load_catalog_dicts,
    load_catalog_json,
)
from contextweaver.routing.graph import ChoiceGraph, ChoiceNode
from contextweaver.routing.labeler import KeywordLabeler
from contextweaver.routing.router import Router, RouteResult
from contextweaver.routing.tree import TreeBuilder

__all__ = [
    "ChoiceCard",
    "ChoiceGraph",
    "ChoiceNode",
    "KeywordLabeler",
    "RouteResult",
    "Router",
    "TreeBuilder",
    "generate_sample_catalog",
    "load_catalog_dicts",
    "load_catalog_json",
    "make_choice_cards",
    "render_cards_text",
]
