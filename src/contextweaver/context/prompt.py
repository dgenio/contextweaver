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
        handle = item.artifact_ref.handle
        # Handles are already namespaced as ``artifact:<id>`` by the firewall
        # (#313); avoid doubling the prefix when wrapping for display.
        inner = handle if handle.startswith("artifact:") else f"artifact:{handle}"
        artifact_note = f" [{inner}]"
    body = _render_body(item)
    return f"[{label}{artifact_note}]\n{body}"


def _render_body(item: ContextItem) -> str:
    """Render the body of a context item, surfacing the tool function name.

    Provider adapters keep the tool's function name in
    ``metadata["function_name"]`` rather than in ``text`` (#308). Fold it into
    the rendered body for tool calls and results so the model can pair a call
    with the result it produced, without mutating ``item.text`` (which would
    break the adapter round-trip invariant from #219).

    Args:
        item: The item whose body is being rendered.

    Returns:
        The body string for inclusion after the section label.
    """
    function_name = item.metadata.get("function_name")
    if isinstance(function_name, str) and function_name:
        if item.kind is ItemKind.tool_call:
            return f"{function_name}({item.text})"
        if item.kind is ItemKind.tool_result:
            return f"{function_name}: {item.text}"
    return item.text


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
