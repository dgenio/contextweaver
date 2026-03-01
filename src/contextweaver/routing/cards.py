"""Choice-card renderer for the contextweaver Routing Engine.

Converts :class:`~contextweaver.types.SelectableItem` objects into compact
:class:`~contextweaver.types.ChoiceCard` instances suitable for inclusion in
LLM prompts.  Full arg schemas are intentionally omitted to minimise token
usage.
"""

from __future__ import annotations

from contextweaver.exceptions import ItemNotFoundError
from contextweaver.routing.catalog import Catalog
from contextweaver.types import ChoiceCard, SelectableItem


def item_to_card(item: SelectableItem) -> ChoiceCard:
    """Convert a SelectableItem to a ChoiceCard.

    The full ``args_schema`` is intentionally omitted to keep prompts compact.

    Args:
        item: The source item.

    Returns:
        A ChoiceCard with ``args_schema`` omitted.
    """
    return ChoiceCard(
        id=item.id,
        name=item.name,
        description=item.description,
        tags=list(item.tags),
        cost_hint=item.cost_hint,
        side_effects=item.side_effects,
    )


def render_cards(items: list[SelectableItem]) -> list[ChoiceCard]:
    """Render a list of items as choice cards.

    Args:
        items: The items to render.

    Returns:
        A list of :class:`~contextweaver.types.ChoiceCard` in the same order.
    """
    return [item_to_card(item) for item in items]


def cards_for_route(route: list[str], catalog: Catalog) -> list[ChoiceCard]:
    """Return choice cards for items that appear in *route* and exist in *catalog*.

    Nodes that are not catalog items (e.g. namespace / category nodes) are
    silently skipped.

    Args:
        route: A list of node IDs from the router.
        catalog: The catalog to look up items in.

    Returns:
        A list of :class:`~contextweaver.types.ChoiceCard` for each matching item.
    """
    cards = []
    for node_id in route:
        try:
            item = catalog.get(node_id)
            cards.append(item_to_card(item))
        except ItemNotFoundError:
            continue
    return cards


def format_card_for_prompt(card: ChoiceCard) -> str:
    """Format a single :class:`~contextweaver.types.ChoiceCard` as a human-readable prompt snippet.

    Args:
        card: The card to format.

    Returns:
        A compact multi-line string suitable for embedding in an LLM prompt.
    """
    lines = [
        f"[{card.id}] {card.name}",
        f"  {card.description}",
    ]
    if card.tags:
        lines.append(f"  tags: {', '.join(sorted(card.tags))}")
    if card.side_effects:
        lines.append("  ⚠ has side effects")
    if card.cost_hint:
        lines.append(f"  cost: {card.cost_hint:.2f}")
    return "\n".join(lines)
