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
from contextweaver.routing.catalog import (
    Catalog,
    CatalogValidationReport,
    ReferenceFinding,
    validate_references,
)
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
from contextweaver.routing.index_cache import (
    CachedRetriever,
    IndexCodec,
    RoutingIndexCache,
    index_fingerprint,
)
from contextweaver.routing.labeler import KeywordLabeler
from contextweaver.routing.navigator import rank_collected
from contextweaver.routing.normalizer import CatalogNormalizer, NormalizationReport
from contextweaver.routing.path import parse_path, resolve_path
from contextweaver.routing.primitive_id import (
    PRIMITIVE_KINDS,
    PrimitiveIdParts,
    canonical_prompt_id,
    canonical_resource_id,
    compute_prompt_hash8,
    compute_resource_hash8,
    format_primitive_id,
    parse_primitive_id,
    resolve_collisions,
)
from contextweaver.routing.router import Router, RouteResult
from contextweaver.routing.selection import (
    SELECTION_SCHEMA_PROVIDERS,
    SelectionValidation,
    selection_schema,
    validate_selection,
)
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
    "CatalogNormalizer",
    "CatalogValidationReport",
    "CachedRetriever",
    "ChoiceGraph",
    "ChoiceNode",
    "IndexCodec",
    "RoutingIndexCache",
    "DEFAULT_CARD_HARD_CAP_TOKENS",
    "DEFAULT_CARD_TARGET_TOKENS",
    "DeterministicScoreProvider",
    "ExecutionFeedback",
    "FeedbackAwareScoreProvider",
    "KeywordLabeler",
    "NormalizationReport",
    "PRIMITIVE_KINDS",
    "PrimitiveIdParts",
    "ReferenceFinding",
    "RouteResult",
    "Router",
    "SELECTION_SCHEMA_PROVIDERS",
    "SchemaSource",
    "SelectionValidation",
    "ToolIdParts",
    "TreeBuilder",
    "aggregate_feedback",
    "bound_browse_response",
    "canonical_prompt_id",
    "canonical_resource_id",
    "canonical_tool_id",
    "cards_for_route",
    "compute_hash8",
    "compute_prompt_hash8",
    "compute_resource_hash8",
    "count_tokens",
    "format_card_for_prompt",
    "format_primitive_id",
    "format_tool_id",
    "hydrate_with_schema",
    "index_fingerprint",
    "item_to_card",
    "lazy_schema_resolver",
    "load_graph",
    "make_choice_cards",
    "parse_path",
    "parse_primitive_id",
    "parse_tool_id",
    "rank_collected",
    "resolve_collisions",
    "render_cards",
    "render_cards_text",
    "resolve_path",
    "save_graph",
    "selection_schema",
    "truncate_description_to_tokens",
    "validate_references",
    "validate_selection",
]
