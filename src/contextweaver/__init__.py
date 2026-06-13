"""contextweaver â€” context firewall and tool router for tool-heavy AI agents.

Two integrated engines:

* **Context Engine** â€” phase-specific budgeted context compilation with a
  context firewall (raw tool outputs stored out-of-band; LLM sees summaries,
  handles, and structured extractions).

* **Routing Engine** â€” bounded-choice navigation over large tool catalogs via
  a DAG + beam search + LLM-friendly choice cards.

Quick start::

    from contextweaver.types import Phase, ContextItem, ItemKind
    from contextweaver.config import ContextBudget

    budget = ContextBudget()
    print(budget.for_phase(Phase.answer))  # 6000
"""

from __future__ import annotations

from contextweaver import (
    config,
    diagnostics,
    envelope,
    exceptions,
    inspection,
    profiles,
    protocols,
    tokens,
    types,
)
from contextweaver._utils import BM25Scorer, FuzzyScorer, TfIdfScorer, jaccard
from contextweaver._version import __version__  # noqa: F401
from contextweaver.config import ContextBudget, ContextPolicy, ScoringConfig
from contextweaver.context.classify import HeuristicSensitivityClassifier, detect_sensitivity
from contextweaver.context.explanation import (
    EXPLANATION_VERSION,
    CandidateExplanation,
    ContextBuildExplanation,
)
from contextweaver.context.firewall_api import (
    CompactResult,
    compact_tool_result,
    firewalled_tool_result,
)
from contextweaver.context.handoff import (
    HANDOFF_CATEGORIES,
    HANDOFF_PACK_VERSION,
    HandoffEntry,
    SessionHandoffPack,
    build_session_handoff_pack,
    render_handoff_pack,
)
from contextweaver.context.manager import ContextManager
from contextweaver.context.memory_source import (
    PHASE_SCOPE_PREFERENCES,
    JsonFixtureMemorySource,
    MemoryEntry,
    memory_entries_to_context_items,
    select_memory_for_phase,
)
from contextweaver.context.secret_redaction import SecretRedactor
from contextweaver.context.sensitivity import (
    MaskRedactionHook,
    register_redaction_hook,
    unregister_redaction_hook,
)
from contextweaver.context.views import ViewRegistry, drilldown_tool_spec, generate_views
from contextweaver.diagnostics import (
    DiagnosticEvent,
    DiagnosticSink,
    InMemoryDiagnosticSink,
    JsonlDiagnosticSink,
    NoOpDiagnosticSink,
    load_diagnostic_events,
    render_diagnostic_report,
    summarize_diagnostics,
)
from contextweaver.envelope import (
    BuildStats,
    ChoiceCard,
    ContextPack,
    DroppedItem,
    FirewallStats,
    HydrationResult,
    ResultEnvelope,
    RoutingDecision,
)
from contextweaver.exceptions import (
    ArtifactNotFoundError,
    ArtifactStoreQuotaError,
    BudgetExceededError,
    BudgetOverflowError,
    CatalogError,
    ConfigError,
    ContextWeaverError,
    DeterminismError,
    DuplicateItemError,
    GraphBuildError,
    ItemNotFoundError,
    PolicyViolationError,
    RouteError,
    StoreClosedError,
)
from contextweaver.inspection import build_inspection_report, render_inspection_report
from contextweaver.metrics import MetricsCollector, MetricsHook
from contextweaver.profiles import Mode, ProfileConfig, RoutingConfig
from contextweaver.protocols import (
    CardPacker,
    ClusteringEngine,
    EmbeddingBackend,
    EpisodicStore,
    EventHook,
    Extractor,
    FactStore,
    HeuristicEstimator,
    Labeler,
    MemorySource,
    Navigator,
    RedactionHook,
    Reranker,
    Retriever,
    RoutingScoreProvider,
    SensitivityClassifier,
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
from contextweaver.routing.feedback import (
    DeterministicScoreProvider,
    ExecutionFeedback,
    FeedbackAwareScoreProvider,
    aggregate_feedback,
)
from contextweaver.routing.graph import ChoiceGraph
from contextweaver.routing.graph_node import ChoiceNode
from contextweaver.routing.history import RouteHistory
from contextweaver.routing.labeler import KeywordLabeler
from contextweaver.routing.manifest import GraphManifest, compute_catalog_hash
from contextweaver.routing.navigator import BeamSearchNavigator
from contextweaver.routing.normalizer import CatalogNormalizer, NormalizationReport
from contextweaver.routing.packer import DefaultCardPacker
from contextweaver.routing.pipeline import RoutingPipeline
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
    JsonFileArtifactStore,
    SqliteEventLog,
    StoreBundle,
)
from contextweaver.summarize.extract import StructuredExtractor
from contextweaver.summarize.rules import RuleBasedSummarizer
from contextweaver.summarize.structured import StructuredFirewall, project
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

__all__ = [
    # sub-modules
    "config",
    "diagnostics",
    "envelope",
    "exceptions",
    "inspection",
    "profiles",
    "protocols",
    "tokens",
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
    "DroppedItem",
    "FirewallStats",
    "HydrationResult",
    "ItemKind",
    "Phase",
    "ResultEnvelope",
    "RoutingDecision",
    "SelectableItem",
    "Sensitivity",
    "ToolCard",
    "ViewSpec",
    "DiagnosticEvent",
    # config
    "ContextBudget",
    "ContextPolicy",
    "Mode",
    "ProfileConfig",
    "RoutingConfig",
    "ScoringConfig",
    # protocols
    "CardPacker",
    "ClusteringEngine",
    "EmbeddingBackend",
    "EpisodicStore",
    "EventHook",
    "Extractor",
    "FactStore",
    "Labeler",
    "MemorySource",
    "Navigator",
    "RedactionHook",
    "Reranker",
    "Retriever",
    "RoutingScoreProvider",
    "SensitivityClassifier",
    "Summarizer",
    "TokenEstimator",
    "HeuristicEstimator",
    "DiagnosticSink",
    # exceptions
    "ArtifactNotFoundError",
    "ArtifactStoreQuotaError",
    "BudgetExceededError",
    "BudgetOverflowError",
    "CatalogError",
    "ConfigError",
    "ContextWeaverError",
    "DeterminismError",
    "DuplicateItemError",
    "GraphBuildError",
    "ItemNotFoundError",
    "PolicyViolationError",
    "RouteError",
    "StoreClosedError",
    # diagnostics
    "InMemoryDiagnosticSink",
    "JsonlDiagnosticSink",
    "NoOpDiagnosticSink",
    "load_diagnostic_events",
    "render_diagnostic_report",
    "summarize_diagnostics",
    "build_inspection_report",
    "render_inspection_report",
    # stores
    "InMemoryArtifactStore",
    "InMemoryEpisodicStore",
    "InMemoryEventLog",
    "InMemoryFactStore",
    "JsonFileArtifactStore",
    "SqliteEventLog",
    "StoreBundle",
    # context engine
    "CandidateExplanation",
    "CompactResult",
    "ContextBuildExplanation",
    "ContextManager",
    "EXPLANATION_VERSION",
    "compact_tool_result",
    "firewalled_tool_result",
    "HANDOFF_CATEGORIES",
    "HANDOFF_PACK_VERSION",
    "HandoffEntry",
    "JsonFixtureMemorySource",
    "HeuristicSensitivityClassifier",
    "MaskRedactionHook",
    "MemoryEntry",
    "PHASE_SCOPE_PREFERENCES",
    "SecretRedactor",
    "SessionHandoffPack",
    "ViewRegistry",
    "build_session_handoff_pack",
    "detect_sensitivity",
    "drilldown_tool_spec",
    "generate_views",
    "memory_entries_to_context_items",
    "register_redaction_hook",
    "unregister_redaction_hook",
    "render_handoff_pack",
    "select_memory_for_phase",
    # observability
    "MetricsCollector",
    "MetricsHook",
    # routing engine
    "BeamSearchNavigator",
    "Catalog",
    "CatalogNormalizer",
    "ChoiceGraph",
    "ChoiceNode",
    "DefaultCardPacker",
    "DeterministicScoreProvider",
    "EngineRegistry",
    "ExecutionFeedback",
    "FeedbackAwareScoreProvider",
    "GraphManifest",
    "JaccardClusteringEngine",
    "KeywordLabeler",
    "NoOpReranker",
    "NormalizationReport",
    "RouteHistory",
    "RouteResult",
    "RouteTrace",
    "Router",
    "RoutingPipeline",
    "TfIdfRetriever",
    "TraceStep",
    "TreeBuilder",
    "aggregate_feedback",
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
    "StructuredFirewall",
    "project",
]
