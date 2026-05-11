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

from contextweaver import config, envelope, exceptions, profiles, protocols, types
from contextweaver._utils import BM25Scorer, FuzzyScorer, TfIdfScorer, jaccard
from contextweaver.config import ContextBudget, ContextPolicy, ScoringConfig
from contextweaver.context.manager import ContextManager
from contextweaver.context.sensitivity import MaskRedactionHook, register_redaction_hook
from contextweaver.context.views import ViewRegistry, drilldown_tool_spec, generate_views
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
    ConfigError,
    ContextWeaverError,
    DuplicateItemError,
    GraphBuildError,
    ItemNotFoundError,
    PolicyViolationError,
    RouteError,
)
from contextweaver.metrics import MetricsCollector, MetricsHook
from contextweaver.profiles import Mode, ProfileConfig, RoutingConfig
from contextweaver.protocols import (
    ClusteringEngine,
    EpisodicStore,
    EventHook,
    Extractor,
    FactStore,
    Labeler,
    RedactionHook,
    Reranker,
    Retriever,
    Summarizer,
    TokenEstimator,
)
from contextweaver.routing.cards import make_choice_cards, render_cards_text
from contextweaver.routing.catalog import (
    Catalog,
    generate_sample_catalog,
    load_catalog,
    load_catalog_dicts,
    load_catalog_json,
    load_catalog_yaml,
)
from contextweaver.routing.graph import ChoiceGraph
from contextweaver.routing.graph_node import ChoiceNode
from contextweaver.routing.labeler import KeywordLabeler
from contextweaver.routing.manifest import GraphManifest, compute_catalog_hash
from contextweaver.routing.normalizer import CatalogNormalizer, NormalizationReport
from contextweaver.routing.registry import (
    EngineRegistry,
    JaccardClusteringEngine,
    NoOpReranker,
    TfIdfRetriever,
    default_registry,
)
from contextweaver.routing.router import Router, RouteResult
from contextweaver.routing.trace import RouteTrace, TraceStep
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

__version__ = "0.3.0"
__all__ = [
    # sub-modules
    "config",
    "envelope",
    "exceptions",
    "profiles",
    "protocols",
    "types",
    # utilities
    "BM25Scorer",
    "FuzzyScorer",
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
    "Mode",
    "ProfileConfig",
    "RoutingConfig",
    "ScoringConfig",
    # protocols
    "ClusteringEngine",
    "EpisodicStore",
    "EventHook",
    "Extractor",
    "FactStore",
    "Labeler",
    "RedactionHook",
    "Reranker",
    "Retriever",
    "Summarizer",
    "TokenEstimator",
    # exceptions
    "ArtifactNotFoundError",
    "BudgetExceededError",
    "CatalogError",
    "ConfigError",
    "ContextWeaverError",
    "DuplicateItemError",
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
    "ViewRegistry",
    "drilldown_tool_spec",
    "generate_views",
    "register_redaction_hook",
    # observability
    "MetricsCollector",
    "MetricsHook",
    # routing engine
    "Catalog",
    "CatalogNormalizer",
    "ChoiceGraph",
    "ChoiceNode",
    "EngineRegistry",
    "GraphManifest",
    "JaccardClusteringEngine",
    "KeywordLabeler",
    "NoOpReranker",
    "NormalizationReport",
    "RouteResult",
    "RouteTrace",
    "Router",
    "TfIdfRetriever",
    "TraceStep",
    "TreeBuilder",
    "compute_catalog_hash",
    "default_registry",
    "generate_sample_catalog",
    "load_catalog",
    "load_catalog_dicts",
    "load_catalog_json",
    "load_catalog_yaml",
    "make_choice_cards",
    "render_cards_text",
    # summarize
    "RuleBasedSummarizer",
    "StructuredExtractor",
]
