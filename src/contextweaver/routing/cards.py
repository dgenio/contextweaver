"""Choice-card renderer for the contextweaver Routing Engine.

Converts :class:`~contextweaver.types.SelectableItem` objects into compact
:class:`~contextweaver.envelope.ChoiceCard` instances suitable for inclusion in
LLM prompts.  Full arg schemas are intentionally omitted to minimise token
usage.

The sizing rules in ``docs/gateway_spec.md`` §2 are expressed in **exact
``cl100k_base`` token counts** rather than characters.  This module's
public API is token-native:

- Single card target ≤ 60 tokens, hard cap ≤ 80 tokens.
- ``tool_browse`` response target ≤ ``60·n``, hard cap ≤ ``80·n + 32``
  (32-token preamble allowance).
- Ordering: descending score, ties broken by ``id`` ascending.

Public API:
    - :func:`item_to_card` — single item → card.
    - :func:`render_cards` — list of items → list of cards (preserves order).
    - :func:`make_choice_cards` — items → bounded card list with
      token-budget truncation per §2.3 / §2.4.
    - :func:`bound_browse_response` — apply the §2.3 ``tool_browse``
      response bound to an already-built card list.
    - :func:`render_cards_text` — cards → numbered text block for prompts.
    - :func:`cards_for_route` — route IDs + catalog → matching cards.
    - :func:`format_card_for_prompt` — single card → multi-line text.
    - :func:`truncate_description_to_tokens` — deterministic
      sentence-aware truncation for §2.4.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import tiktoken

from contextweaver.envelope import ChoiceCard
from contextweaver.exceptions import CatalogError, ItemNotFoundError
from contextweaver.routing.catalog import Catalog
from contextweaver.types import SelectableItem

_logger = logging.getLogger("contextweaver.routing.cards")

# Lazy ``cl100k_base`` encoder for §2.3 token accounting.  Loaded on
# first use because :func:`tiktoken.get_encoding` downloads the BPE file
# from a network URL on the first call in environments without a
# pre-warmed cache.  In offline / air-gapped environments the load
# fails; we fall back to a deterministic chars-per-token estimate
# (``len(text) // 4``) that matches the existing
# :class:`~contextweaver.protocols.TiktokenEstimator` fallback and keeps
# the §2.3 bounds enforced — at the cost of approximate-rather-than-exact
# token counts.
_ENCODER: Any | None = None
_ENCODER_FAILED: bool = False


def _get_encoder() -> Any | None:  # noqa: ANN401 — tiktoken Encoding is not typed
    """Return the cached cl100k_base encoder, or ``None`` if offline.

    Logs a single warning on first failure, mirroring
    :class:`~contextweaver.protocols.TiktokenEstimator`'s offline behaviour.
    """
    global _ENCODER, _ENCODER_FAILED
    if _ENCODER is not None:
        return _ENCODER
    if _ENCODER_FAILED:
        return None
    try:
        _ENCODER = tiktoken.get_encoding("cl100k_base")
        return _ENCODER
    except Exception as exc:  # pragma: no cover - exercised in offline tests
        _ENCODER_FAILED = True
        _logger.warning(
            "tiktoken cl100k_base encoding unavailable (%s); falling back to "
            "chars/4 token estimate for §2.3 budget enforcement.",
            exc,
        )
        return None


# §2.3 default token budgets.
DEFAULT_CARD_TARGET_TOKENS = 60
DEFAULT_CARD_HARD_CAP_TOKENS = 80
DEFAULT_BROWSE_PREAMBLE_TOKENS = 32

# §2.4 sentence terminators considered when truncating descriptions.
_SENTENCE_BOUNDARY_RE = re.compile(r"[.!?](?:\s|$)")

# §2.4 ellipsis character (U+2026).
_ELLIPSIS = "…"


def count_tokens(text: str) -> int:
    """Return the ``cl100k_base`` token count of *text*.

    Falls back to ``len(text) // 4`` if the encoding is unavailable (e.g.
    offline environment without a pre-warmed tiktoken cache).  The
    fallback is deterministic so the §2.3 bounds remain meaningful even
    in air-gapped CI.
    """
    if not text:
        return 0
    enc = _get_encoder()
    if enc is not None:
        return len(enc.encode(text))
    return len(text) // 4 or 1


def truncate_description_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate *text* deterministically to at most *max_tokens* tokens (§2.4).

    Algorithm (deterministic, sentence-boundary-aware):

    1. If *text* already fits, return it verbatim.
    2. Otherwise, find the longest sentence-terminated prefix that fits.
    3. If no sentence boundary fits, hard-cut to the byte offset whose
       token count is ``max_tokens - 1`` and append ``"…"`` (U+2026).

    Args:
        text: The description to truncate.
        max_tokens: Maximum tokens permitted.  Values ``<= 0`` return ``""``.

    Returns:
        The truncated description, stable for the same input.
    """
    if max_tokens <= 0:
        return ""
    if count_tokens(text) <= max_tokens:
        return text

    # Try sentence boundaries from longest prefix down.
    boundaries = [m.end() for m in _SENTENCE_BOUNDARY_RE.finditer(text)]
    for end in reversed(boundaries):
        candidate = text[:end].rstrip()
        if count_tokens(candidate) <= max_tokens:
            return candidate

    # No sentence boundary fits — hard-cut to (max_tokens - 1) tokens and
    # append the ellipsis.  Binary search over character offsets to find
    # the largest prefix whose encoded length is below the cap.
    target = max(max_tokens - 1, 0)
    lo, hi = 0, len(text)
    best = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        if count_tokens(text[:mid]) <= target:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return text[:best].rstrip() + _ELLIPSIS


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
    # §2.1 caps: name ≤ 64 chars, tags ≤ 5 entries (each ≤ 24 chars).
    capped_name = item.name[:64]
    capped_tags = sorted({t[:24] for t in item.tags})[:5]
    return ChoiceCard(
        id=item.id,
        name=capped_name,
        description=item.description,
        tags=capped_tags,
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


def _card_token_count(card: ChoiceCard) -> int:
    """Approximate the rendered token cost of a single card.

    Matches the format produced by :func:`render_cards_text` so that the
    sum of per-card costs is a tight upper bound on the rendered length.
    """
    tag_str = f" [{', '.join(card.tags)}]" if card.tags else ""
    score_str = f" score={card.score:.2f}" if card.score is not None else ""
    line = f"[1/1] {card.id} ({card.kind}) — {card.description}{tag_str}{score_str}"
    return count_tokens(line)


def _enforce_card_budget(
    card: ChoiceCard,
    *,
    target_tokens: int,
    hard_cap_tokens: int,
) -> ChoiceCard:
    """Truncate *card.description* to keep the card under the §2.3 budget.

    The card builder MUST NOT silently drop fields — truncation is
    confined to ``description``.  Raises :class:`CatalogError` if the
    card still exceeds *hard_cap_tokens* after maximal truncation
    (e.g. an unreasonably long tags list).
    """
    if _card_token_count(card) <= target_tokens:
        return card

    # Truncate description until the card fits the target, then verify
    # the hard cap.  The non-description content (id, name, kind, tags,
    # score) is fixed-size for a given card, so the description budget
    # is target_tokens - (card_tokens - description_tokens).
    fixed_overhead = _card_token_count(
        ChoiceCard(
            id=card.id,
            name=card.name,
            description="",
            tags=card.tags,
            kind=card.kind,
            namespace=card.namespace,
            has_schema=card.has_schema,
            score=card.score,
            cost_hint=card.cost_hint,
            side_effects=card.side_effects,
        )
    )
    description_budget = max(target_tokens - fixed_overhead, 1)
    truncated_desc = truncate_description_to_tokens(card.description, description_budget)
    truncated = ChoiceCard(
        id=card.id,
        name=card.name,
        description=truncated_desc,
        tags=card.tags,
        kind=card.kind,
        namespace=card.namespace,
        has_schema=card.has_schema,
        score=card.score,
        cost_hint=card.cost_hint,
        side_effects=card.side_effects,
    )
    final_tokens = _card_token_count(truncated)
    if final_tokens > hard_cap_tokens:
        raise CatalogError(
            f"ChoiceCard for {card.id!r} exceeds hard cap "
            f"({final_tokens} > {hard_cap_tokens} tokens) "
            "after description truncation; non-description fields too large."
        )
    return truncated


def _sort_cards_for_browse(cards: list[ChoiceCard]) -> list[ChoiceCard]:
    """Apply the §2.5 deterministic ordering: score desc, id asc."""
    return sorted(cards, key=lambda c: (-(c.score or 0.0), c.id))


def make_choice_cards(
    items: list[SelectableItem],
    *,
    max_cards: int = 20,
    target_tokens_per_card: int = DEFAULT_CARD_TARGET_TOKENS,
    hard_cap_tokens_per_card: int = DEFAULT_CARD_HARD_CAP_TOKENS,
    scores: dict[str, float] | None = None,
) -> list[ChoiceCard]:
    """Create a bounded list of :class:`ChoiceCard` objects.

    Each card is rendered with a description truncated to fit
    *target_tokens_per_card* (per §2.4).  Cards are then sorted per
    §2.5 (score desc, ``id`` asc) and capped to *max_cards*.

    Args:
        items: Source items.
        max_cards: Maximum number of cards to return.
        target_tokens_per_card: Target per-card token budget (§2.3).
            Defaults to :data:`DEFAULT_CARD_TARGET_TOKENS` (60).
        hard_cap_tokens_per_card: Hard cap per card; truncation that
            still exceeds this cap raises :class:`CatalogError`.
            Defaults to :data:`DEFAULT_CARD_HARD_CAP_TOKENS` (80).
        scores: Optional mapping of item-id → score.  When absent, the
            original input order is preserved.

    Returns:
        A list of :class:`ChoiceCard` objects.

    Raises:
        CatalogError: If any card cannot be truncated below the hard cap.
    """
    score_map = scores or {}
    cards = [
        _enforce_card_budget(
            item_to_card(item, score=score_map.get(item.id)),
            target_tokens=target_tokens_per_card,
            hard_cap_tokens=hard_cap_tokens_per_card,
        )
        for item in items
    ]

    if score_map:
        cards = _sort_cards_for_browse(cards)

    return cards[:max_cards]


def bound_browse_response(
    cards: list[ChoiceCard],
    *,
    target_tokens_per_card: int = DEFAULT_CARD_TARGET_TOKENS,
    hard_cap_tokens_per_card: int = DEFAULT_CARD_HARD_CAP_TOKENS,
    preamble_tokens: int = DEFAULT_BROWSE_PREAMBLE_TOKENS,
) -> list[ChoiceCard]:
    """Apply the §2.3 ``tool_browse`` response bound.

    Drops the lowest-scoring cards (tail of the §2.5 ordering) until the
    total rendered token count fits ``hard_cap_per_card * n +
    preamble_tokens``.  Each retained card has already been individually
    bounded by :func:`_enforce_card_budget`.

    Args:
        cards: Cards produced by :func:`make_choice_cards`.
        target_tokens_per_card: §2.3 target.  Defaults to 60.
        hard_cap_tokens_per_card: §2.3 hard cap.  Defaults to 80.
        preamble_tokens: §2.3 preamble allowance.  Defaults to 32.

    Returns:
        A possibly shortened list of cards whose summed rendered tokens
        fit the §2.3 bound.
    """
    ordered = _sort_cards_for_browse(list(cards))
    enforced = [
        _enforce_card_budget(
            c,
            target_tokens=target_tokens_per_card,
            hard_cap_tokens=hard_cap_tokens_per_card,
        )
        for c in ordered
    ]
    # Drop from the tail (lowest score, highest id) until we fit.
    while enforced:
        total = sum(_card_token_count(c) for c in enforced) + preamble_tokens
        cap = hard_cap_tokens_per_card * len(enforced) + preamble_tokens
        if total <= cap:
            break
        enforced.pop()
    return enforced


def render_cards_text(cards: list[ChoiceCard]) -> str:
    """Render cards as a numbered text block for LLM prompts.

    Format per line::

        [1/5] billing:invoices_search (tool) — Search invoices by date [billing, search] score=0.82

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
            f"[{idx}/{total}] {card.id} ({card.kind}) — {card.description}{tags_str}{score_str}"
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
