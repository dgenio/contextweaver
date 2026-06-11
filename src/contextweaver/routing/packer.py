"""Card-packing stage of the routing pipeline (issue #56).

Implements :class:`~contextweaver.protocols.CardPacker`.  The default
implementation wraps :func:`contextweaver.routing.cards.make_choice_cards`
to preserve every byte of pre-refactor output: same ordering, same per-card
truncation, same prompt-cache-stability guarantee (issue #218).

A *budget_tokens* hint controls the cumulative number of cards returned
(soft cap — the underlying card renderer enforces per-card hard caps).
``budget_tokens=None`` disables the cap and matches pre-refactor behaviour.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from contextweaver.routing.cards import make_choice_cards
from contextweaver.tokens import heuristic_counter

if TYPE_CHECKING:
    from contextweaver.envelope import ChoiceCard
    from contextweaver.types import SelectableItem

# Deterministic, env-independent counter for the packer's coarse cumulative-cap
# estimate.  Per-card §2.3 hard caps stay exact (tiktoken) in ``cards.py``; this
# soft cap only needs a reproducible upper bound, so it uses the script-aware
# heuristic to avoid varying with tiktoken-cache availability (issues #493/#530).
_HEURISTIC = heuristic_counter()


def _estimate_card_tokens(card: ChoiceCard) -> int:
    """Cheap, deterministic token estimate for a card's cumulative-budget cap.

    The :class:`~contextweaver.routing.cards` renderer already pins each
    card to a target-token budget per §2.4; the packer only needs a coarse
    upper bound for the cumulative-budget cap. Routing it through the shared
    :func:`contextweaver.tokens.heuristic_counter` keeps it on the single
    source of truth rather than a stray ``// 4`` literal (issue #493) while
    staying reproducible across environments.
    """
    parts = [card.id, card.namespace or "", card.name, card.kind, card.description]
    parts.extend(card.tags)
    return _HEURISTIC.estimate(" ".join(p for p in parts if p))


class DefaultCardPacker:
    """Default :class:`~contextweaver.protocols.CardPacker`.

    Args:
        max_cards: Hard upper bound on the number of cards returned.
            Defaults to 20, matching
            :func:`~contextweaver.routing.cards.make_choice_cards`.
        target_tokens_per_card: Per-card target budget (forwarded).
        hard_cap_tokens_per_card: Per-card hard cap (forwarded).
    """

    def __init__(
        self,
        *,
        max_cards: int = 20,
        target_tokens_per_card: int | None = None,
        hard_cap_tokens_per_card: int | None = None,
    ) -> None:
        self._max_cards = max_cards
        self._target_tokens_per_card = target_tokens_per_card
        self._hard_cap_tokens_per_card = hard_cap_tokens_per_card

    def pack(
        self,
        items: list[SelectableItem],
        scores: dict[str, float],
        *,
        budget_tokens: int | None = None,
    ) -> list[ChoiceCard]:
        """Render *items* as :class:`ChoiceCard` and apply *budget_tokens* soft cap."""
        kwargs: dict[str, int] = {"max_cards": self._max_cards}
        if self._target_tokens_per_card is not None:
            kwargs["target_tokens_per_card"] = self._target_tokens_per_card
        if self._hard_cap_tokens_per_card is not None:
            kwargs["hard_cap_tokens_per_card"] = self._hard_cap_tokens_per_card
        cards = make_choice_cards(items, scores=scores, **kwargs)
        if budget_tokens is None or not cards:
            return cards
        used = 0
        out: list[ChoiceCard] = []
        for card in cards:
            est = _estimate_card_tokens(card)
            if used + est > budget_tokens and out:
                break
            out.append(card)
            used += est
        return out
