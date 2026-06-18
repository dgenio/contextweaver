"""Context sub-package for contextweaver.

Exports the Context Engine components: candidate generation, scoring,
deduplication, selection, firewall, prompt rendering, and the high-level
:class:`~contextweaver.context.manager.ContextManager`.
"""

from __future__ import annotations

from contextweaver.context.call_prompt import build_schema_header
from contextweaver.context.candidates import generate_candidates, resolve_dependency_closure
from contextweaver.context.consolidation import (
    cluster_episodes,
    consolidate,
    decay_episodes,
    decay_facts,
    promote_clusters,
)
from contextweaver.context.consolidation_types import (
    CONSOLIDATION_REPORT_VERSION,
    ConsolidationPolicy,
    ConsolidationReport,
    EpisodeCluster,
    PromotedFact,
)
from contextweaver.context.dedup import deduplicate_candidates
from contextweaver.context.explanation import (
    EXPLANATION_VERSION,
    CandidateExplanation,
    ContextBuildExplanation,
)
from contextweaver.context.firewall import apply_firewall, apply_firewall_to_batch
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
from contextweaver.context.prompt import passthrough_renderer, render_context, render_item
from contextweaver.context.scoring import score_candidates, score_item
from contextweaver.context.selection import select_and_pack
from contextweaver.context.sensitivity import (
    MaskRedactionHook,
    apply_sensitivity_filter,
    register_redaction_hook,
    unregister_redaction_hook,
)
from contextweaver.context.views import ViewRegistry, drilldown_tool_spec, generate_views

__all__ = [
    "CONSOLIDATION_REPORT_VERSION",
    "CandidateExplanation",
    "ConsolidationPolicy",
    "ConsolidationReport",
    "ContextBuildExplanation",
    "ContextManager",
    "EXPLANATION_VERSION",
    "EpisodeCluster",
    "HANDOFF_CATEGORIES",
    "HANDOFF_PACK_VERSION",
    "HandoffEntry",
    "JsonFixtureMemorySource",
    "MaskRedactionHook",
    "MemoryEntry",
    "PHASE_SCOPE_PREFERENCES",
    "PromotedFact",
    "SessionHandoffPack",
    "ViewRegistry",
    "apply_firewall",
    "apply_firewall_to_batch",
    "apply_sensitivity_filter",
    "build_schema_header",
    "build_session_handoff_pack",
    "cluster_episodes",
    "consolidate",
    "decay_episodes",
    "decay_facts",
    "deduplicate_candidates",
    "drilldown_tool_spec",
    "generate_candidates",
    "generate_views",
    "memory_entries_to_context_items",
    "passthrough_renderer",
    "promote_clusters",
    "register_redaction_hook",
    "unregister_redaction_hook",
    "render_context",
    "render_handoff_pack",
    "render_item",
    "resolve_dependency_closure",
    "score_candidates",
    "score_item",
    "select_and_pack",
    "select_memory_for_phase",
]
