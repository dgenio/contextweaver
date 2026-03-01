"""Prompt renderer for the contextweaver Context Engine (Stage 5).

Deterministic rendering to prompt sections + PromptBuilder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from contextweaver.types import ContextItem, ItemKind, Phase

if TYPE_CHECKING:
    pass

# Section heading templates per item kind
_SECTION_LABELS: dict[ItemKind, str] = {
    ItemKind.USER_TURN: "USER",
    ItemKind.AGENT_MSG: "ASSISTANT",
    ItemKind.TOOL_CALL: "TOOL CALL",
    ItemKind.TOOL_RESULT: "TOOL RESULT",
    ItemKind.DOC_SNIPPET: "DOCUMENT",
    ItemKind.MEMORY_FACT: "FACT",
    ItemKind.PLAN_STATE: "PLAN",
    ItemKind.POLICY: "POLICY",
}

# Section order by phase
_PHASE_SECTIONS: dict[Phase, list[str]] = {
    Phase.ROUTE: ["facts", "episodic_summary", "recent_turns", "goal"],
    Phase.CALL: ["facts", "tool_call_context", "constraints"],
    Phase.INTERPRET: ["facts", "tool_result_summary", "prior_evidence"],
    Phase.ANSWER: ["facts", "evidence", "provenance_refs"],
}


def render_context(
    items: list[ContextItem],
    episodic_summaries: list[str],
    facts: dict[str, str],
    phase: Phase,
) -> tuple[str, dict[str, int]]:
    """Deterministic rendering to prompt sections.

    Returns: (rendered_text, tokens_per_section dict)
    """
    sections: list[str] = []
    tokens_per_section: dict[str, int] = {}

    # Facts section
    if facts:
        fact_lines = [f"- {k}: {v}" for k, v in sorted(facts.items())]
        fact_text = "## Known Facts\n" + "\n".join(fact_lines)
        sections.append(fact_text)
        tokens_per_section["facts"] = len(fact_text) // 4

    # Episodic summaries section
    if episodic_summaries:
        ep_text = "## Recent Context\n" + "\n".join(f"- {s}" for s in episodic_summaries)
        sections.append(ep_text)
        tokens_per_section["episodic"] = len(ep_text) // 4

    # Context items section
    if items:
        item_lines = []
        for item in items:
            label = _SECTION_LABELS.get(item.kind, item.kind.value.upper())
            artifact_note = ""
            if item.artifact_ref:
                artifact_note = f" [artifact:{item.artifact_ref}]"
            item_lines.append(f"[{label}{artifact_note}]\n{item.text}")
        item_text = "\n\n".join(item_lines)
        sections.append(item_text)
        tokens_per_section["context_items"] = len(item_text) // 4

    rendered = "\n\n".join(sections)
    return rendered, tokens_per_section


@dataclass
class _PromptPlan:
    """Private IR. Not public in v0.1."""

    phase: Phase = Phase.ANSWER
    goal: str = ""
    budget_tokens: int = 6000
    instructions: str = ""
    required_output_schema: dict[str, Any] = field(default_factory=dict)
    choice_cards: list[Any] = field(default_factory=list)
    allowed_context_kinds: list[ItemKind] = field(default_factory=list)
    notes: dict[str, Any] = field(default_factory=dict)


_PHASE_PREAMBLE: dict[Phase, str] = {
    Phase.ROUTE: "You are selecting tools/agents to use. Choose from the options below.",
    Phase.CALL: "You are preparing to call a tool. Use the context provided.",
    Phase.INTERPRET: "You are interpreting tool results. Analyze the output below.",
    Phase.ANSWER: "You are answering the user. Use the evidence and context provided.",
}


class PromptBuilder:
    """Build phase-specific prompts from context packs and choice cards."""

    async def build_prompt(
        self,
        goal: str,
        phase: Phase,
        context_pack: Any,
        choice_cards: list[Any] | None = None,
    ) -> str:
        """Build a full prompt string."""
        return self.build_prompt_sync(goal, phase, context_pack, choice_cards)

    def build_prompt_sync(
        self,
        goal: str,
        phase: Phase,
        context_pack: Any,
        choice_cards: list[Any] | None = None,
    ) -> str:
        """Synchronous prompt builder.

        Prompt structure:
        1. Phase-specific system preamble
        2. Semantic facts section
        3. Episodic summary section
        4. Context section
        5. Choice cards section (if provided)
        6. Available artifact handles
        7. Goal/instruction section
        8. Output format guidance
        """
        parts: list[str] = []

        # 1. Preamble
        preamble = _PHASE_PREAMBLE.get(phase, "")
        if preamble:
            parts.append(f"## Instructions\n{preamble}")

        # 2-4. Rendered context (includes facts, episodic, items)
        if hasattr(context_pack, "rendered_text") and context_pack.rendered_text:
            parts.append(context_pack.rendered_text)

        # 5. Choice cards
        if choice_cards:
            from contextweaver.routing.cards import render_cards_text

            cards_text = render_cards_text(choice_cards)
            parts.append(f"## Available Tools\n{cards_text}")

        # 6. Artifact handles
        if hasattr(context_pack, "artifacts_available") and context_pack.artifacts_available:
            handles = ", ".join(context_pack.artifacts_available)
            parts.append(f"## Artifacts Available for Drilldown\n{handles}")

        # 7. Goal
        if goal:
            parts.append(f"## Goal\n{goal}")

        # 8. Format guidance
        if phase == Phase.ROUTE:
            parts.append("## Output\nSelect one or more tools by ID.")
        elif phase == Phase.ANSWER:
            parts.append("## Output\nProvide a clear, evidence-based answer.")

        return "\n\n".join(parts)
