"""Choice-card renderer for the contextweaver Routing Engine.

Converts :class:`~contextweaver.types.SelectableItem` objects into compact
:class:`~contextweaver.envelope.ChoiceCard` instances suitable for inclusion in
LLM prompts.  Full arg schemas are intentionally omitted to minimise token
usage.

Public API:
    - :func:`item_to_card` — single item → card
    - :func:`render_cards` — list of items → list of cards (preserves order)
    - :func:`make_choice_cards` — items → bounded card list with truncation
    - :func:`render_cards_text` — cards → numbered text block for prompts
    - :func:`cards_for_route` — route IDs + catalog → matching cards
    - :func:`format_card_for_prompt` — single card → multi-line text
"""

from __future__ import annotations

from contextweaver.envelope import ChoiceCard
from contextweaver.exceptions import ItemNotFoundError
from contextweaver.routing.catalog import Catalog
from contextweaver.types import SelectableItem


def item_to_card(
    item: SelectableItem,
    *,
    score: float | None = None,
) -> ChoiceCard:
    """Convert a :class:`SelectableItem` to a :class:`ChoiceCard`.

    The full ``args_schema`` is intentionally omitted to keep prompts compact.
    ``has_schema`` is set to ``True`` when the source item has a non-empty
    ``args_schema``.

    Args:
        item: The source item.
        score: Optional relevance score to attach.

    Returns:
        A :class:`ChoiceCard` with ``args_schema`` omitted.
    """
    return ChoiceCard(
        id=item.id,
        name=item.name,
        description=item.description,
        tags=list(item.tags),
        kind=item.kind,
        namespace=item.namespace,
        has_schema=bool(item.args_schema),
        score=score,
        cost_hint=item.cost_hint,
        side_effects=item.side_effects,
    )


def render_cards(items: list[SelectableItem]) -> list[ChoiceCard]:
    """Render a list of items as choice cards.

    Args:
        items: The items to render.

    Returns:
        A list of :class:`ChoiceCard` in the same order.
    """
    return [item_to_card(item) for item in items]


def make_choice_cards(
    items: list[SelectableItem],
    *,
    max_choices: int = 20,
    max_desc_chars: int = 240,
    max_total_chars: int | None = None,
    scores: dict[str, float] | None = None,
) -> list[ChoiceCard]:
    """Create a bounded list of :class:`ChoiceCard` objects.

    Descriptions longer than *max_desc_chars* are truncated with ``"..."``.
    If *max_total_chars* is set the lowest-scored cards are dropped until the
    rendered text fits.

    Cards are ordered by score descending.  When scores are equal (or absent),
    the original input order is preserved as a stable tie-break.

    Args:
        items: Source items.
        max_choices: Maximum number of cards to return.
        max_desc_chars: Maximum description length before truncation
            (clamped to a minimum of 4).
        max_total_chars: Optional cap on total rendered text length.
        scores: Optional mapping of item-id → score.

    Returns:
        A list of :class:`ChoiceCard` objects.
    """
    # Need at least 4 chars to produce "X..." (1 visible + "...")
    max_desc_chars = max(max_desc_chars, 4)
    score_map = scores or {}

    cards: list[ChoiceCard] = []
    for item in items:
        card = item_to_card(item, score=score_map.get(item.id))
        # Truncate description
        if len(card.description) > max_desc_chars:
            card.description = card.description[: max_desc_chars - 3] + "..."
        cards.append(card)

    # Cap at max_choices — keep highest-scored, tie-break by original index
    if len(cards) > max_choices:
        indexed = list(enumerate(cards))
        indexed.sort(key=lambda t: (-(t[1].score or 0.0), t[0]))
        cards = [c for _, c in indexed[:max_choices]]

    # Honour max_total_chars by dropping lowest-scored tail
    if max_total_chars is not None:
        indexed = list(enumerate(cards))
        indexed.sort(key=lambda t: (-(t[1].score or 0.0), t[0]))
        while indexed and len(render_cards_text([c for _, c in indexed])) > max_total_chars:
            indexed.pop()
        cards = [c for _, c in indexed]

    return cards


def render_cards_text(cards: list[ChoiceCard]) -> str:
    """Render cards as a numbered text block for LLM prompts.

    Format per line::

        [1/5] billing.invoices.search (tool) — Search invoices by date [billing, search] score=0.82

    Score is shown **only** when ``card.score is not None``.

    Args:
        cards: The cards to render.

    Returns:
        A newline-separated string.
    """
    total = len(cards)
    lines: list[str] = []
    for idx, card in enumerate(cards, 1):
        tags_str = f" [{', '.join(sorted(card.tags))}]" if card.tags else ""
        score_str = f" score={card.score:.2f}" if card.score is not None else ""
        lines.append(
            f"[{idx}/{total}] {card.id} ({card.kind}) "
            f"\u2014 {card.description}{tags_str}{score_str}"
        )
    return "\n".join(lines)


def cards_for_route(route: list[str], catalog: Catalog) -> list[ChoiceCard]:
    """Return choice cards for items that appear in *route* and exist in *catalog*.

    Nodes that are not catalog items (e.g. namespace / category nodes) are
    silently skipped.

    Args:
        route: A list of node IDs from the router.
        catalog: The catalog to look up items in.

    Returns:
        A list of :class:`ChoiceCard` for each matching item.
    """
    cards: list[ChoiceCard] = []
    for node_id in route:
        try:
            item = catalog.get(node_id)
            cards.append(item_to_card(item))
        except ItemNotFoundError:
            continue
    return cards


def format_card_for_prompt(card: ChoiceCard) -> str:
    """Format a single :class:`ChoiceCard` as a human-readable prompt snippet.

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
        lines.append("  ! has side effects")
    if card.cost_hint:
        lines.append(f"  cost: {card.cost_hint:.2f}")
    return "\n".join(lines)
