"""Choice-card renderer for the contextweaver Routing Engine.

Compact, LLM-friendly. Never includes full schemas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from contextweaver.types import SelectableItem


@dataclass
class ChoiceCard:
    """Compact, LLM-friendly representation of a SelectableItem.

    Never includes full schemas. Deterministic serialization.
    """

    id: str
    kind: str
    name: str
    description: str
    tags: list[str] = field(default_factory=list)
    namespace: str = ""
    has_schema: bool = False
    score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        d: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "description": self.description,
            "tags": list(self.tags),
            "namespace": self.namespace,
            "has_schema": self.has_schema,
        }
        if self.score is not None:
            d["score"] = self.score
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChoiceCard:
        """Deserialise from a JSON-compatible dict."""
        return cls(
            id=data["id"],
            kind=data.get("kind", "tool"),
            name=data["name"],
            description=data["description"],
            tags=list(data.get("tags", [])),
            namespace=data.get("namespace", ""),
            has_schema=bool(data.get("has_schema", False)),
            score=data.get("score"),
        )


def make_choice_cards(
    items: list[SelectableItem],
    *,
    max_choices: int = 20,
    max_desc_chars: int = 240,
    max_total_chars: int | None = None,
    scores: dict[str, float] | None = None,
) -> list[ChoiceCard]:
    """Convert items to ChoiceCards with enforced limits.

    Enforces: len <= max_choices, desc <= max_desc_chars (truncated with "..."),
    total chars <= max_total_chars (drops lowest-scored), no schemas.
    """
    cards = []
    for item in items[:max_choices]:
        desc = item.description
        if len(desc) > max_desc_chars:
            desc = desc[: max_desc_chars - 3] + "..."
        card = ChoiceCard(
            id=item.id,
            kind=item.kind,
            name=item.name,
            description=desc,
            tags=list(item.tags),
            namespace=item.namespace,
            has_schema=item.args_schema is not None,
            score=scores.get(item.id) if scores else None,
        )
        cards.append(card)

    # Enforce total chars limit
    if max_total_chars is not None:
        total = sum(len(render_card_line(c, i, len(cards))) for i, c in enumerate(cards))
        while total > max_total_chars and len(cards) > 1:
            # Drop lowest-scored card
            if any(c.score is not None for c in cards):
                min_card = min(
                    cards,
                    key=lambda c: (c.score if c.score is not None else float("inf"), c.id),
                )
                cards.remove(min_card)
            else:
                cards.pop()
            total = sum(len(render_card_line(c, i, len(cards))) for i, c in enumerate(cards))

    return cards


def render_card_line(card: ChoiceCard, index: int, total: int) -> str:
    """Render a single card as a one-line string."""
    parts = [f"[{index + 1}/{total}]"]
    parts.append(f"{card.name} ({card.kind})")
    parts.append(f"— {card.description}")
    if card.tags:
        parts.append(f"[{', '.join(card.tags)}]")
    if card.score is not None:
        parts.append(f"score={card.score:.2f}")
    return " ".join(parts)


def render_cards_text(cards: list[ChoiceCard]) -> str:
    """One line per card, numbered.

    [1/5] billing.invoices.search (tool) — Search invoices [billing, search] score=0.82
    """
    lines = []
    for i, card in enumerate(cards):
        lines.append(render_card_line(card, i, len(cards)))
    return "\n".join(lines)
