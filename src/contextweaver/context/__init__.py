"""Context sub-package for contextweaver."""

from contextweaver.context.candidates import generate_candidates
from contextweaver.context.dedup import deduplicate_candidates
from contextweaver.context.firewall import apply_firewall
from contextweaver.context.manager import ContextManager, ContextPack
from contextweaver.context.prompt import PromptBuilder, render_context
from contextweaver.context.scoring import score_candidates
from contextweaver.context.selection import select_and_pack

__all__ = [
    "ContextManager",
    "ContextPack",
    "PromptBuilder",
    "apply_firewall",
    "deduplicate_candidates",
    "generate_candidates",
    "render_context",
    "score_candidates",
    "select_and_pack",
]
