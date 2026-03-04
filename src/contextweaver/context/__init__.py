"""Context sub-package for contextweaver.

Exports the Context Engine components: candidate generation, scoring,
deduplication, selection, firewall, prompt rendering, and the high-level
:class:`~contextweaver.context.manager.ContextManager`.
"""

from __future__ import annotations

from contextweaver.context.call_prompt import build_schema_header
from contextweaver.context.candidates import generate_candidates, resolve_dependency_closure
from contextweaver.context.dedup import deduplicate_candidates
from contextweaver.context.firewall import apply_firewall, apply_firewall_to_batch
from contextweaver.context.manager import ContextManager
from contextweaver.context.prompt import render_context, render_item
from contextweaver.context.scoring import score_candidates, score_item
from contextweaver.context.selection import select_and_pack
from contextweaver.context.sensitivity import MaskRedactionHook, apply_sensitivity_filter

__all__ = [
    "ContextManager",
    "MaskRedactionHook",
    "apply_firewall",
    "apply_firewall_to_batch",
    "apply_sensitivity_filter",
    "build_schema_header",
    "deduplicate_candidates",
    "generate_candidates",
    "render_context",
    "render_item",
    "resolve_dependency_closure",
    "score_candidates",
    "score_item",
    "select_and_pack",
]
