"""Prompt renderer for the contextweaver Context Engine.

Converts a list of selected :class:`~contextweaver.types.ContextItem` objects
into a formatted prompt string ready for injection into an LLM call.
"""

from __future__ import annotations

from contextweaver.types import ContextItem, ItemKind

# Section heading templates per item kind
_SECTION_LABELS: dict[ItemKind, str] = {
    ItemKind.user_turn: "USER",
    ItemKind.agent_msg: "ASSISTANT",
    ItemKind.tool_call: "TOOL CALL",
    ItemKind.tool_result: "TOOL RESULT",
    ItemKind.doc_snippet: "DOCUMENT",
    ItemKind.memory_fact: "FACT",
    ItemKind.plan_state: "PLAN",
    ItemKind.policy: "POLICY",
}


def render_item(item: ContextItem) -> str:
    """Render a single :class:`~contextweaver.types.ContextItem` as a prompt snippet.

    Args:
        item: The item to render.

    Returns:
        A formatted string suitable for inclusion in a prompt.
    """
    label = _SECTION_LABELS.get(item.kind, item.kind.value.upper())
    artifact_note = ""
    if item.artifact_ref:
        artifact_note = f" [artifact:{item.artifact_ref.handle}]"
    return f"[{label}{artifact_note}]\n{item.text}"


def render_context(
    items: list[ContextItem],
    separator: str = "\n\n",
    header: str = "",
    footer: str = "",
) -> str:
    """Render a list of context items into a single prompt string.

    Args:
        items: Ordered list of items to include.
        separator: String inserted between rendered items.
        header: Optional prefix inserted before all items.
        footer: Optional suffix appended after all items.

    Returns:
        The assembled prompt string.
    """
    rendered = [render_item(item) for item in items]
    body = separator.join(rendered)
    parts = []
    if header:
        parts.append(header)
    if body:
        parts.append(body)
    if footer:
        parts.append(footer)
    return separator.join(parts) if parts else ""
