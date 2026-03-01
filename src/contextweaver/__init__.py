"""contextweaver -- dynamic context management for tool-using AI agents.

Two integrated engines:

* **Context Engine** -- phase-specific budgeted context compilation with a
  context firewall.
* **Routing Engine** -- bounded-choice navigation over large tool catalogs.
"""

from __future__ import annotations

# Shared utilities
from contextweaver._utils import TfIdfScorer

# Config
from contextweaver.config import ContextBudget, ContextPolicy, ScoringConfig

# Context engine
from contextweaver.context.manager import ContextManager, ContextPack
from contextweaver.context.prompt import PromptBuilder

# Errors
from contextweaver.exceptions import (
    ArtifactNotFoundError,
    BudgetExceededError,
    CatalogError,
    ContextWeaverError,
    GraphBuildError,
    ItemNotFoundError,
    PolicyViolationError,
    RouteError,
)

# Protocols
from contextweaver.protocols import (
    CharDivFourEstimator,
    EventHook,
    Extractor,
    Labeler,
    NoOpHook,
    RedactionHook,
    Summarizer,
    TokenEstimator,
)
from contextweaver.routing.cards import ChoiceCard, make_choice_cards, render_cards_text
from contextweaver.routing.catalog import (
    generate_sample_catalog,
    load_catalog_dicts,
    load_catalog_json,
)

# Routing engine
from contextweaver.routing.graph import ChoiceGraph, ChoiceNode
from contextweaver.routing.labeler import KeywordLabeler
from contextweaver.routing.router import Router, RouteResult
from contextweaver.routing.tree import TreeBuilder

# Stores (protocols + defaults + bundle)
from contextweaver.store import StoreBundle
from contextweaver.store.artifacts import InMemoryArtifactStore
from contextweaver.store.episodic import InMemoryEpisodicStore
from contextweaver.store.event_log import InMemoryEventLog
from contextweaver.store.facts import InMemoryFactStore
from contextweaver.summarize.extract import StructuredExtractor

# Summarization + extraction
from contextweaver.summarize.rules import RuleBasedSummarizer

# Types
from contextweaver.types import (
    ArtifactRef,
    BuildStats,
    ContextItem,
    ItemKind,
    Phase,
    ResultEnvelope,
    SelectableItem,
    Sensitivity,
    ToolCard,
    ViewSpec,
)

__version__ = "0.1.0"

__all__ = [
    # Types
    "ArtifactRef",
    "BuildStats",
    "ContextItem",
    "ItemKind",
    "Phase",
    "ResultEnvelope",
    "SelectableItem",
    "Sensitivity",
    "ToolCard",
    "ViewSpec",
    # Config
    "ContextBudget",
    "ContextPolicy",
    "ScoringConfig",
    # Protocols
    "CharDivFourEstimator",
    "EventHook",
    "Extractor",
    "Labeler",
    "NoOpHook",
    "RedactionHook",
    "Summarizer",
    "TokenEstimator",
    # Errors
    "ArtifactNotFoundError",
    "BudgetExceededError",
    "CatalogError",
    "ContextWeaverError",
    "GraphBuildError",
    "ItemNotFoundError",
    "PolicyViolationError",
    "RouteError",
    # Stores
    "InMemoryArtifactStore",
    "InMemoryEpisodicStore",
    "InMemoryEventLog",
    "InMemoryFactStore",
    "StoreBundle",
    # Context engine
    "ContextManager",
    "ContextPack",
    "PromptBuilder",
    # Routing engine
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
    # Summarization
    "RuleBasedSummarizer",
    "StructuredExtractor",
    # Utilities
    "TfIdfScorer",
]
