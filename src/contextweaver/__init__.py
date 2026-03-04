"""contextweaver — dynamic context management for tool-using AI agents.

Two integrated engines:

* **Context Engine** — phase-specific budgeted context compilation with a
  context firewall (raw tool outputs stored out-of-band; LLM sees summaries,
  handles, and structured extractions).

* **Routing Engine** — bounded-choice navigation over large tool catalogs via
  a DAG + beam search + LLM-friendly choice cards.

Quick start::

    from contextweaver.types import Phase, ContextItem, ItemKind
    from contextweaver.config import ContextBudget

    budget = ContextBudget()
    print(budget.for_phase(Phase.answer))  # 6000
"""

from __future__ import annotations

from contextweaver import config, envelope, exceptions, protocols, types
from contextweaver._utils import TfIdfScorer, jaccard
from contextweaver.config import ContextBudget, ContextPolicy, ScoringConfig
from contextweaver.context.manager import ContextManager
from contextweaver.context.sensitivity import MaskRedactionHook
from contextweaver.envelope import (
    BuildStats,
    ChoiceCard,
    ContextPack,
    HydrationResult,
    ResultEnvelope,
)
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
from contextweaver.protocols import (
    EventHook,
    Extractor,
    Labeler,
    RedactionHook,
    Summarizer,
    TokenEstimator,
)
from contextweaver.routing.cards import make_choice_cards, render_cards_text
from contextweaver.routing.catalog import (
    Catalog,
    generate_sample_catalog,
    load_catalog_dicts,
    load_catalog_json,
)
from contextweaver.routing.graph import ChoiceGraph
from contextweaver.routing.graph_node import ChoiceNode
from contextweaver.routing.labeler import KeywordLabeler
from contextweaver.routing.router import Router, RouteResult
from contextweaver.routing.tree import TreeBuilder
from contextweaver.store import (
    InMemoryArtifactStore,
    InMemoryEpisodicStore,
    InMemoryEventLog,
    InMemoryFactStore,
    StoreBundle,
)
from contextweaver.summarize.extract import StructuredExtractor
from contextweaver.summarize.rules import RuleBasedSummarizer
from contextweaver.types import (
    ArtifactRef,
    ContextItem,
    ItemKind,
    Phase,
    SelectableItem,
    Sensitivity,
    ToolCard,
    ViewSpec,
)

__version__ = "0.1.1"
__all__ = [
    # sub-modules
    "config",
    "envelope",
    "exceptions",
    "protocols",
    "types",
    # utilities
    "TfIdfScorer",
    "jaccard",
    # types / enums
    "ArtifactRef",
    "BuildStats",
    "ChoiceCard",
    "ContextItem",
    "ContextPack",
    "HydrationResult",
    "ItemKind",
    "Phase",
    "ResultEnvelope",
    "SelectableItem",
    "Sensitivity",
    "ToolCard",
    "ViewSpec",
    # config
    "ContextBudget",
    "ContextPolicy",
    "ScoringConfig",
    # protocols
    "EventHook",
    "Extractor",
    "Labeler",
    "RedactionHook",
    "Summarizer",
    "TokenEstimator",
    # exceptions
    "ArtifactNotFoundError",
    "BudgetExceededError",
    "CatalogError",
    "ContextWeaverError",
    "GraphBuildError",
    "ItemNotFoundError",
    "PolicyViolationError",
    "RouteError",
    # stores
    "InMemoryArtifactStore",
    "InMemoryEpisodicStore",
    "InMemoryEventLog",
    "InMemoryFactStore",
    "StoreBundle",
    # context engine
    "ContextManager",
    "MaskRedactionHook",
    # routing engine
    "Catalog",
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
    # summarize
    "RuleBasedSummarizer",
    "StructuredExtractor",
]
