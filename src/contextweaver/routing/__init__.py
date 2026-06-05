"""Routing sub-package for contextweaver.

Exports the catalog, graph, labeler, tree builder, router, and card renderer.
"""

from __future__ import annotations

from contextweaver.routing.cards import (
    DEFAULT_CARD_HARD_CAP_TOKENS,
    DEFAULT_CARD_TARGET_TOKENS,
    bound_browse_response,
    cards_for_route,
    count_tokens,
    format_card_for_prompt,
    item_to_card,
    make_choice_cards,
    render_cards,
    render_cards_text,
    truncate_description_to_tokens,
)
from contextweaver.routing.catalog import Catalog
from contextweaver.routing.feedback import (
    DeterministicScoreProvider,
    ExecutionFeedback,
    FeedbackAwareScoreProvider,
    aggregate_feedback,
)
from contextweaver.routing.graph import ChoiceGraph
from contextweaver.routing.graph_io import load_graph, save_graph
from contextweaver.routing.graph_node import ChoiceNode
from contextweaver.routing.hydration import (
    SchemaSource,
    hydrate_with_schema,
    lazy_schema_resolver,
)
from contextweaver.routing.labeler import KeywordLabeler
from contextweaver.routing.navigator import rank_collected
from contextweaver.routing.path import parse_path, resolve_path
from contextweaver.routing.router import Router, RouteResult
from contextweaver.routing.tool_id import (
    ToolIdParts,
    canonical_tool_id,
    compute_hash8,
    format_tool_id,
    parse_tool_id,
)
from contextweaver.routing.tree import TreeBuilder

__all__ = [
    "Catalog",
    "ChoiceGraph",
    "ChoiceNode",
    "DEFAULT_CARD_HARD_CAP_TOKENS",
    "DEFAULT_CARD_TARGET_TOKENS",
    "DeterministicScoreProvider",
    "ExecutionFeedback",
    "FeedbackAwareScoreProvider",
    "KeywordLabeler",
    "RouteResult",
    "Router",
    "SchemaSource",
    "ToolIdParts",
    "TreeBuilder",
    "aggregate_feedback",
    "bound_browse_response",
    "canonical_tool_id",
    "cards_for_route",
    "compute_hash8",
    "count_tokens",
    "format_card_for_prompt",
    "format_tool_id",
    "hydrate_with_schema",
    "item_to_card",
    "lazy_schema_resolver",
    "load_graph",
    "make_choice_cards",
    "parse_path",
    "parse_tool_id",
    "rank_collected",
    "render_cards",
    "render_cards_text",
    "resolve_path",
    "save_graph",
    "truncate_description_to_tokens",
]
