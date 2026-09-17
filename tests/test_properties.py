"""Property-based tests for deterministic, security-grade pure functions (#755).

The suite is otherwise entirely example-based; these Hypothesis properties
cover the input space that hand-enumerated cases miss, focused on the
invariants that matter most:

* ``secrets.scrub_secrets`` — idempotence, never re-introduces a masked secret,
  and masks a known secret shape wherever it appears in free text.
* Token estimators — determinism, ``estimate("") == 0``, and monotonicity under
  concatenation (appending text never lowers the estimate).
* ``tests/fixtures._normalize.to_canonical_json`` — round-trip idempotence.
* ``context.consolidation.cluster_episodes`` — determinism, order-independence,
  and idempotence (the clustering is documented as stable for identical input).

Kept fast and deterministic: no external I/O, bounded example sizes.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from contextweaver.context.candidates import resolve_dependency_closure
from contextweaver.context.consolidation import cluster_episodes
from contextweaver.context.dedup import deduplicate_candidates
from contextweaver.envelope import (
    CHOICE_CARD_KINDS,
    CHOICE_CARD_NAME_MAX_LEN,
    CHOICE_CARD_SAFETY_LEVELS,
    CHOICE_CARD_TAG_MAX_LEN,
    CHOICE_CARD_TAGS_MAX_COUNT,
    ChoiceCard,
)
from contextweaver.exceptions import CatalogError
from contextweaver.protocols import CharDivFourEstimator, HeuristicEstimator
from contextweaver.routing.cards import make_choice_cards
from contextweaver.routing.packer import DefaultCardPacker, _estimate_card_tokens
from contextweaver.secrets import DEFAULT_SECRET_MASK, scrub_secrets
from contextweaver.store.episodic import Episode
from contextweaver.store.event_log import InMemoryEventLog
from contextweaver.types import ContextItem, ItemKind, SelectableItem, Sensitivity
from tests.fixtures._normalize import to_canonical_json

# ---------------------------------------------------------------------------
# secrets.scrub_secrets — security-grade; property targets per #755
# ---------------------------------------------------------------------------


@given(st.text())
def test_scrub_secrets_is_idempotent(text: str) -> None:
    """Scrubbing an already-scrubbed string is a no-op (stable fixed point)."""
    once = scrub_secrets(text)
    assert scrub_secrets(once) == once


@given(st.text())
def test_scrub_secrets_never_reintroduces_secrets(text: str) -> None:
    """The mask itself is never treated as a secret and re-masked."""
    scrubbed = scrub_secrets(text)
    # The mask may appear (from masking) but must survive a second pass intact:
    # count of the mask token does not grow when re-scrubbing.
    assert scrub_secrets(scrubbed).count(DEFAULT_SECRET_MASK) == scrubbed.count(DEFAULT_SECRET_MASK)


# AWS access-key ids: a fixed prefix + 16 upper-case base32 chars.
_aws_keys = st.from_regex(r"AKIA[A-Z0-9]{16}", fullmatch=True)


@given(
    prefix=st.text(alphabet=st.characters(blacklist_categories=("Cc",)), max_size=40), key=_aws_keys
)
def test_scrub_secrets_masks_known_secret_shape(prefix: str, key: str) -> None:
    """A recognised secret embedded in free text is removed and masked.

    Guard against the key being adjacent to word characters that would break
    the ``\\b`` boundary the pattern relies on: separate with a space.
    """
    text = f"{prefix} token={key} tail"
    scrubbed = scrub_secrets(text)
    assert key not in scrubbed
    assert DEFAULT_SECRET_MASK in scrubbed


# ---------------------------------------------------------------------------
# Token estimators — determinism / zero / monotonicity
# ---------------------------------------------------------------------------


@given(st.text())
def test_heuristic_estimator_deterministic_and_nonnegative(text: str) -> None:
    est = HeuristicEstimator()
    first = est.estimate(text)
    assert first == est.estimate(text)
    assert first >= 0


def test_heuristic_estimator_empty_is_zero() -> None:
    assert HeuristicEstimator().estimate("") == 0
    assert CharDivFourEstimator().estimate("") == 0


@given(a=st.text(), b=st.text())
def test_heuristic_estimator_monotonic_under_concatenation(a: str, b: str) -> None:
    """Appending text never lowers the estimate (both scripts count >= 0)."""
    est = HeuristicEstimator()
    assert est.estimate(a) <= est.estimate(a + b)
    assert est.estimate(b) <= est.estimate(a + b)


# ---------------------------------------------------------------------------
# to_canonical_json — round-trip idempotence
# ---------------------------------------------------------------------------

_json_scalars = st.none() | st.booleans() | st.integers() | st.text()
_json_values = st.recursive(
    _json_scalars,
    lambda children: (
        st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=8), children, max_size=4)
    ),
    max_leaves=15,
)


@given(_json_values)
def test_to_canonical_json_is_idempotent(payload: object) -> None:
    """Canonicalising, reloading, and re-canonicalising is stable."""
    once = to_canonical_json(payload)
    assert to_canonical_json(json.loads(once)) == once


# ---------------------------------------------------------------------------
# cluster_episodes — determinism / order-independence / idempotence
# ---------------------------------------------------------------------------


def _clustering(episodes: list[Episode], threshold: float) -> list[list[str]]:
    """Return the partition as a sorted list of sorted id-groups."""
    clusters = cluster_episodes(episodes, similarity_threshold=threshold)
    return sorted(sorted(c.episode_ids) for c in clusters)


_episodes = st.lists(
    st.builds(
        Episode,
        episode_id=st.text(alphabet="abcdefghijklmnop0123456789", min_size=1, max_size=6),
        summary=st.text(alphabet="the quick brown fox jumps over lazy dog ", max_size=30),
    ),
    max_size=8,
    unique_by=lambda ep: ep.episode_id,
)


@given(_episodes, st.floats(min_value=0.0, max_value=1.0))
def test_cluster_episodes_is_deterministic(episodes: list[Episode], threshold: float) -> None:
    assert _clustering(episodes, threshold) == _clustering(episodes, threshold)


@given(_episodes, st.floats(min_value=0.0, max_value=1.0))
def test_cluster_episodes_partitions_every_episode(
    episodes: list[Episode], threshold: float
) -> None:
    """Every input episode lands in exactly one cluster (a true partition)."""
    groups = _clustering(episodes, threshold)
    flat = [eid for group in groups for eid in group]
    assert sorted(flat) == sorted(ep.episode_id for ep in episodes)
    assert len(flat) == len(set(flat))  # no episode duplicated across clusters


# ---------------------------------------------------------------------------
# Property 3 — serde round-trip (issue #755)
# ---------------------------------------------------------------------------

# JSON-compatible metadata values. ``ContextItem.to_dict`` copies metadata
# through unchanged, so anything not JSON-representable would fail at the
# ``json.dumps`` boundary rather than in the dataclass — which is the boundary
# that actually matters, since that is how items reach a store or the wire.
_json_scalars = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(10**9), max_value=10**9),
    st.text(max_size=40),
)

# Deliberately broad text: astral-plane characters, surrogate-free but
# otherwise unrestricted, plus the empty string. #755 asks for "broad
# Unicode/empty/boundary values", and the empty string is the boundary that
# most often breaks a hand-rolled serialiser.
_broad_text = st.text(max_size=120)

_context_items = st.builds(
    ContextItem,
    id=st.text(min_size=1, max_size=16),
    kind=st.sampled_from(list(ItemKind)),
    text=_broad_text,
    token_estimate=st.integers(min_value=0, max_value=10**6),
    sensitivity=st.sampled_from(list(Sensitivity)),
    metadata=st.dictionaries(st.text(max_size=20), _json_scalars, max_size=5),
    parent_id=st.one_of(st.none(), st.text(min_size=1, max_size=16)),
)


@given(_context_items)
def test_context_item_round_trips_through_json(item: ContextItem) -> None:
    """A ContextItem survives to_dict -> JSON -> from_dict unchanged.

    Round-tripped through real ``json.dumps``/``json.loads`` rather than
    dict-to-dict. A dict-only round-trip would pass for values JSON cannot
    represent, which makes it a test of two dataclass methods agreeing with
    each other rather than of the serialisation contract anything downstream
    depends on.
    """
    restored = ContextItem.from_dict(json.loads(json.dumps(item.to_dict())))

    assert restored == item


@given(_context_items)
def test_context_item_to_dict_is_idempotent_under_round_trip(item: ContextItem) -> None:
    """The serialised form is stable: re-serialising a restored item matches.

    Distinct from equality above. A dataclass can compare equal while its
    serialised form drifts — a default re-applied, a None dropped on one pass
    and kept on the next — and it is the serialised form that is stored and
    diffed.
    """
    once = item.to_dict()
    twice = ContextItem.from_dict(json.loads(json.dumps(once))).to_dict()

    assert twice == once


# ---------------------------------------------------------------------------
# Property 5 — dedup idempotence (issue #755)
# ---------------------------------------------------------------------------


# ``deduplicate_candidates`` documents its input as "a list of (score, item)
# tuples in *descending* score order (as returned by score_candidates)", so the
# strategy sorts. Feeding it unsorted input would test behaviour outside the
# stated contract and then report the result as a defect in it.
# ``draw`` is intentionally unannotated: ``st.DrawFn`` does not exist in
# hypothesis 6.0.0, which is what the gating floor-deps job installs from the
# declared ``hypothesis>=6`` floor (verified on 3.10 — absent at 6.0.0, present
# by 6.30.0). tests/ is outside mypy's scope, so the annotation bought nothing.
@st.composite
def _scored_candidates(draw) -> list[tuple[float, ContextItem]]:  # noqa: ANN001
    items = draw(st.lists(_context_items, max_size=8))
    scores = draw(
        st.lists(
            st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
            min_size=len(items),
            max_size=len(items),
        )
    )
    return sorted(zip(scores, items, strict=True), key=lambda pair: pair[0], reverse=True)


@given(_scored_candidates(), st.floats(min_value=0.0, max_value=1.0, allow_nan=False))
def test_deduplicate_candidates_is_idempotent(
    scored: list[tuple[float, ContextItem]], threshold: float
) -> None:
    """Applying deterministic dedup twice is equivalent to applying it once.

    The greedy pass keeps an item only when it is below threshold against
    every item already kept. Re-running over the survivors therefore compares
    each one against exactly the set it already cleared, so nothing may drop
    on a second pass. If this ever fails, the pass has acquired a dependency
    on something other than the items it is looking at.
    """
    once, _ = deduplicate_candidates(scored, threshold)
    twice, removed_second = deduplicate_candidates(once, threshold)

    assert twice == once
    assert removed_second == 0


@given(_scored_candidates(), st.floats(min_value=0.0, max_value=1.0, allow_nan=False))
def test_deduplicate_candidates_only_ever_drops(
    scored: list[tuple[float, ContextItem]], threshold: float
) -> None:
    """Dedup removes; it never invents, reorders or edits.

    Guards the half idempotence cannot see: a pass that replaced every item
    with a merged copy could still be idempotent. The docstring promises "the
    filtered list in the same order", and ``removed_count`` must account for
    the difference exactly — a count that drifts from the list is how a
    downstream BuildStats figure starts lying.
    """
    kept, removed = deduplicate_candidates(scored, threshold)

    assert len(kept) + removed == len(scored)

    # Order-preserving subsequence. Matched on the identity of the ContextItem
    # rather than of the ``(score, item)`` tuple: the pass rebuilds the tuple
    # (``kept.append((score, item))``), so tuple identity is not part of the
    # contract, while substituting a *different item object* would be.
    cursor = 0
    for score, item in kept:
        while cursor < len(scored) and scored[cursor][1] is not item:
            cursor += 1
        assert cursor < len(scored), "dedup returned an item that was not in its input"
        assert scored[cursor][0] == score, "dedup altered an item's score"
        cursor += 1


# ---------------------------------------------------------------------------
# Property 4 — dependency closure (issue #755)
# ---------------------------------------------------------------------------


# A log whose parent links form a forest, plus a subset of it to resolve.
# Parents always point at an EARLIER item, so the strategy cannot generate a
# cycle -- ``resolve_dependency_closure`` walks the chain with a ``while`` loop
# and a cycle would hang it. That is a real (if unreachable) property of the
# function, not something these tests should discover by timing out; it is
# noted here rather than asserted because nothing in the pipeline can build one:
# ``parent_id`` is assigned from an already-appended item.
@st.composite
def _log_and_subset(draw) -> tuple[InMemoryEventLog, list[ContextItem]]:  # noqa: ANN001
    size = draw(st.integers(min_value=0, max_value=10))
    log = InMemoryEventLog()
    items: list[ContextItem] = []
    for index in range(size):
        # None, or any strictly-earlier item: a forest, never a cycle.
        parent_choice = draw(st.integers(min_value=-1, max_value=index - 1))
        items.append(
            ContextItem(
                id=f"i{index}",
                kind=draw(st.sampled_from(list(ItemKind))),
                text=draw(st.text(max_size=40)),
                parent_id=None if parent_choice < 0 else f"i{parent_choice}",
            )
        )
    for item in items:
        log.append(item)

    keep = draw(st.lists(st.booleans(), min_size=size, max_size=size))
    subset = [item for item, taken in zip(items, keep, strict=True) if taken]
    return log, subset


@given(_log_and_subset())
def test_dependency_closure_leaves_no_orphan(
    case: tuple[InMemoryEventLog, list[ContextItem]],
) -> None:
    """The invariant the pass exists for: no survivor is missing its parent.

    #755 states it as "included dependent results never become orphaned from
    required parents". Everything reachable by walking ``parent_id`` upward
    must be present in the result, not merely the immediate parent -- a pass
    that added one level and stopped would satisfy a shallower assertion.
    """
    log, subset = case
    resolved, _ = resolve_dependency_closure(subset, log)

    present = {item.id for item in resolved}
    for item in resolved:
        parent_id = item.parent_id
        while parent_id is not None:
            assert parent_id in present, (
                f"{item.id} survived without ancestor {parent_id}, which the log holds"
            )
            parent_id = log.get(parent_id).parent_id


@given(_log_and_subset())
def test_dependency_closure_only_adds(
    case: tuple[InMemoryEventLog, list[ContextItem]],
) -> None:
    """Closure is additive, order-preserving, and its count is truthful.

    The precondition is that the candidates come from the log, which is how
    ``build.py`` calls it (``generate_candidates`` reads the same log). Worth
    stating because the function ends by filtering through ``event_log.all()``,
    so an item that is *not* in the log would be silently dropped rather than
    kept -- outside the contract, and not asserted here.
    """
    log, subset = case
    resolved, closures = resolve_dependency_closure(subset, log)

    resolved_ids = [item.id for item in resolved]
    assert set(subset_ids := {item.id for item in subset}) <= set(resolved_ids)
    assert len(resolved_ids) == len(subset_ids) + closures
    assert len(resolved_ids) == len(set(resolved_ids)), "closure duplicated an item"

    # Output follows log order, which is what makes the result stable to read.
    log_order = [item.id for item in log.all()]
    assert resolved_ids == [item_id for item_id in log_order if item_id in set(resolved_ids)]


@given(_log_and_subset())
def test_dependency_closure_is_idempotent(
    case: tuple[InMemoryEventLog, list[ContextItem]],
) -> None:
    """Re-resolving an already-closed set pulls in nothing further.

    If this fails the pass is not reaching a fixed point in one sweep, which
    would make the result depend on how many times the pipeline happened to
    call it.
    """
    log, subset = case
    once, _ = resolve_dependency_closure(subset, log)
    twice, closures_second = resolve_dependency_closure(once, log)

    assert [item.id for item in twice] == [item.id for item in once]
    assert closures_second == 0


# ---------------------------------------------------------------------------
# Properties 1, 2 and 7 — card packing: budget, determinism, structural bounds
# (issue #755)
# ---------------------------------------------------------------------------


# Items deliberately adversarial for the renderer: names and tags well past the
# §2 caps, wide/astral Unicode (where a character is not a byte and not a
# token), empty strings, and duplicate *content* under unique ids. Ids stay
# unique because the §2.5 ordering contract is "score desc, id asc" -- with
# duplicate ids the tie-break is unspecified, which is outside the contract
# rather than something these tests should pin.
@st.composite
def _selectable_items(  # noqa: ANN201
    draw,  # noqa: ANN001
    min_size: int = 0,
    max_size: int = 8,
) -> list[SelectableItem]:
    size = draw(st.integers(min_value=min_size, max_value=max_size))
    text = st.text(max_size=120)
    # Deliberately biased to OVERSIZED, not merely "up to 120": ``st.text`` shrinks
    # toward short strings, so a plain ``max_size=120`` almost never emits a name
    # past the 64-char cap and the bound below goes untested. Measured -- with the
    # unbiased strategy, deleting ``capped_name = name[:64]`` from cards.py left
    # this module fully green.
    #
    # ASCII, and tags kept few and short-ish, for a reason worth recording: the
    # per-card HARD cap is 80 tokens (``DEFAULT_CARD_HARD_CAP_TOKENS``) and
    # ``_card_token_count`` counts id, kind, description, tags and score -- but
    # NOT the name. So an oversized *name* is free and exercises its cap for
    # nothing, while a handful of long astral-plane *tags* blows the hard cap and
    # sends every draw down the CatalogError path, which is a different contract
    # (see ``test_make_choice_cards_refuses_a_card_it_cannot_fit`` below) and
    # would leave these properties vacuous.
    ascii_text = st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126))
    over_name = st.one_of(text, ascii_text.filter(lambda s: len(s) > 64) | st.just("n" * 80))
    over_tag = st.one_of(st.text(max_size=20), st.just("t" * 30))
    return [
        SelectableItem(
            id=f"item-{index}",
            kind=draw(st.sampled_from(CHOICE_CARD_KINDS)),
            name=draw(over_name),
            description=draw(text),
            tags=draw(st.lists(over_tag, max_size=7)),
            namespace=draw(st.text(max_size=16)),
            side_effects=draw(st.booleans()),
            cost_hint=draw(st.floats(min_value=0.0, max_value=1e3, allow_nan=False)),
        )
        for index in range(size)
    ]


@st.composite
def _items_and_scores(draw) -> tuple[list[SelectableItem], dict[str, float]]:  # noqa: ANN001
    items = draw(_selectable_items())
    scores = {
        item.id: draw(st.floats(min_value=-1e3, max_value=1e3, allow_nan=False)) for item in items
    }
    return items, scores


def _render_or_overflow(
    items: list[SelectableItem], scores: dict[str, float], **kwargs: object
) -> list[ChoiceCard] | None:
    """Render cards, or return ``None`` when the documented overflow path fires.

    ``_truncate_card`` raises :class:`CatalogError` when a card's
    non-description fields alone exceed the per-card hard cap
    (``cards.py:279-284``). #755 item 1 states the contract as "never exceed the
    configured pack budget according to the contract, **or fail through the
    documented explicit overflow path**" -- so both outcomes are in contract,
    and a property that tolerated only the first would fail for the wrong
    reason.

    This matters more than it looks. ``_card_token_count`` uses tiktoken when
    the encoding is available and a heuristic when it is not, so the SAME input
    can render in one environment and overflow in another. CI has the encoding;
    a sandbox whose egress proxy blocks ``openaipublic.blob.core.windows.net``
    does not. A property that assumed rendering is therefore green locally and
    red in CI -- which is exactly how this was found, on #887's first push.

    ``test_a_small_plain_catalog_still_renders`` is the guard-the-guard: if
    every draw started overflowing, the properties below would quietly assert
    nothing, and that test would fail instead.
    """
    try:
        return make_choice_cards(items, scores=scores, **kwargs)  # type: ignore[arg-type]
    except CatalogError:
        return None


def test_a_small_plain_catalog_still_renders() -> None:
    """Guard-the-guard for ``_render_or_overflow``'s ``None`` branch.

    The properties that use it skip their assertions on overflow. If a change
    made *every* card overflow they would all pass while checking nothing, so
    something has to fail instead. This is that something.
    """
    items = [
        SelectableItem(id=f"item-{index}", kind="tool", name=f"n{index}", description="d")
        for index in range(3)
    ]
    cards = make_choice_cards(items, scores={f"item-{i}": 0.5 for i in range(3)})
    assert len(cards) == 3


def test_make_choice_cards_refuses_a_card_it_cannot_fit() -> None:
    """#755 item 1's *documented explicit overflow path*, asserted directly.

    The alternative to silently emitting an oversized card is refusing it, and
    the refusal has to name the card to be actionable. ``hard_cap_tokens_per_card=1``
    makes this deterministic whichever tokenizer is installed.
    """
    item = SelectableItem(id="item-0", kind="tool", name="n", description="d" * 200)
    with pytest.raises(CatalogError, match="exceeds hard cap"):
        make_choice_cards(
            [item],
            scores={"item-0": 1.0},
            target_tokens_per_card=1,
            hard_cap_tokens_per_card=1,
        )


@given(_items_and_scores(), st.integers(min_value=1, max_value=30))
def test_make_choice_cards_respects_structural_bounds(
    case: tuple[list[SelectableItem], dict[str, float]],
    max_cards: int,
) -> None:
    """#755 item 7: rendered cards always satisfy the gateway-spec §2 bounds.

    This is a real discriminator despite ``ChoiceCard.__post_init__`` enforcing
    the same bounds: the renderer is what has to *truncate* arbitrary input to
    fit them, so a truncation bug surfaces here as a ``ValidationError`` raised
    inside ``make_choice_cards`` rather than as a failed assertion. Asserting
    the bounds again afterwards keeps the property honest if that constructor
    check is ever relaxed.

    Astral-plane text is the case worth generating: a naive character slice can
    keep a count under the cap while the spec's intent is about prompt size.
    """
    items, scores = case
    cards = _render_or_overflow(items, scores, max_cards=max_cards)
    if cards is None:
        return  # documented overflow path; see _render_or_overflow

    assert len(cards) <= max_cards
    assert len(cards) <= len(items), "renderer invented a card"
    for card in cards:
        assert len(card.name) <= CHOICE_CARD_NAME_MAX_LEN
        assert len(card.tags) <= CHOICE_CARD_TAGS_MAX_COUNT
        assert all(len(tag) <= CHOICE_CARD_TAG_MAX_LEN for tag in card.tags)
        assert card.kind in CHOICE_CARD_KINDS
        assert card.safety in CHOICE_CARD_SAFETY_LEVELS


@given(_items_and_scores(), st.integers(min_value=1, max_value=30))
def test_make_choice_cards_is_ordered_by_score_desc_then_id_asc(
    case: tuple[list[SelectableItem], dict[str, float]],
    max_cards: int,
) -> None:
    """#755 items 2/7: the §2.5 ordering that prompt-cache stability rests on.

    Issue #218 lets downstream assemblers place a cache breakpoint after the
    last card, which is only safe if the order is a total function of the
    inputs. Asserting the *rule* rather than just call-to-call equality is what
    catches a renderer that is stably wrong.
    """
    items, scores = case
    cards = _render_or_overflow(items, scores, max_cards=max_cards)
    if cards is None:
        return  # documented overflow path; see _render_or_overflow

    keys = [(-(card.score if card.score is not None else 0.0), card.id) for card in cards]
    assert keys == sorted(keys), f"cards not in (score desc, id asc) order: {keys}"


@given(_items_and_scores(), st.integers(min_value=1, max_value=30))
def test_make_choice_cards_is_deterministic(
    case: tuple[list[SelectableItem], dict[str, float]],
    max_cards: int,
) -> None:
    """#755 item 2: identical inputs render byte-identical cards (issue #218)."""
    items, scores = case
    first = _render_or_overflow(items, scores, max_cards=max_cards)
    second = _render_or_overflow(items, scores, max_cards=max_cards)
    if first is None:
        assert second is None, "overflow is not deterministic"
        return

    assert second is not None
    assert [card.to_dict() for card in first] == [card.to_dict() for card in second]


def _pack_or_overflow(
    items: list[SelectableItem], scores: dict[str, float], *, budget_tokens: int | None
) -> list[ChoiceCard] | None:
    """``DefaultCardPacker.pack`` through the same overflow tolerance.

    ``pack`` calls ``make_choice_cards`` internally, so it inherits the
    :class:`CatalogError` path described on :func:`_render_or_overflow`.
    """
    try:
        return DefaultCardPacker().pack(items, scores, budget_tokens=budget_tokens)
    except CatalogError:
        return None


@given(_items_and_scores(), st.integers(min_value=1, max_value=150))
def test_pack_respects_budget_unless_one_card_alone_exceeds_it(
    case: tuple[list[SelectableItem], dict[str, float]],
    budget_tokens: int,
) -> None:
    """#755 item 1: the packer's real cumulative-budget contract.

    ``budget_tokens`` is documented as a *soft* cap, and the implementation's
    ``and out`` guard means the first card is emitted even when it alone busts
    the budget -- there is no silent empty result. So the honest property is
    "within budget, **or** exactly one card", not "always within budget". A
    change that started dropping the first card, or that let a second card
    over the line, fails this.
    """
    items, scores = case
    cards = _pack_or_overflow(items, scores, budget_tokens=budget_tokens)
    if cards is None:
        return  # documented overflow path; see _render_or_overflow

    used = sum(_estimate_card_tokens(card) for card in cards)
    assert used <= budget_tokens or len(cards) == 1, (
        f"{len(cards)} cards estimated at {used} tokens against a {budget_tokens} budget"
    )
    if items:
        assert cards, "budgeting emptied a non-empty pack; the first card is never dropped"


@given(_items_and_scores(), st.integers(min_value=1, max_value=150))
def test_pack_returns_a_prefix_of_the_unbudgeted_pack(
    case: tuple[list[SelectableItem], dict[str, float]],
    budget_tokens: int,
) -> None:
    """#755 item 1: budgeting only truncates -- it never reorders or rewrites.

    If the budget could change *which* cards appear, or their order, the
    prompt-cache guarantee of #218 would not survive a budget change, and two
    callers differing only in budget would disagree about card content.
    """
    items, scores = case
    full = _pack_or_overflow(items, scores, budget_tokens=None)
    capped = _pack_or_overflow(items, scores, budget_tokens=budget_tokens)
    if full is None or capped is None:
        return  # documented overflow path; see _render_or_overflow

    assert [card.to_dict() for card in capped] == [card.to_dict() for card in full[: len(capped)]]


@given(
    _items_and_scores(),
    st.integers(min_value=1, max_value=150),
    st.integers(min_value=0, max_value=150),
)
def test_pack_is_monotonic_in_budget(
    case: tuple[list[SelectableItem], dict[str, float]],
    budget_tokens: int,
    extra: int,
) -> None:
    """#755 item 1: raising the budget never returns fewer cards.

    A non-monotonic cap is the shape of bug that makes capacity planning
    impossible -- paying for more budget and getting less context.
    """
    items, scores = case
    smaller = _pack_or_overflow(items, scores, budget_tokens=budget_tokens)
    larger = _pack_or_overflow(items, scores, budget_tokens=budget_tokens + extra)
    if smaller is None or larger is None:
        return  # documented overflow path; see _render_or_overflow

    assert len(larger) >= len(smaller)


@given(_items_and_scores(), st.integers(min_value=1, max_value=150))
def test_pack_is_deterministic(
    case: tuple[list[SelectableItem], dict[str, float]],
    budget_tokens: int,
) -> None:
    """#755 item 2: the packer adds no nondeterminism on top of the renderer.

    The cumulative estimate deliberately uses the script-aware heuristic rather
    than tiktoken (issues #493/#530) so the cap does not move with cache
    availability; this pins that the whole stage is reproducible.
    """
    items, scores = case
    first = _pack_or_overflow(items, scores, budget_tokens=budget_tokens)
    second = _pack_or_overflow(items, scores, budget_tokens=budget_tokens)
    if first is None:
        assert second is None, "overflow is not deterministic"
        return
    assert second is not None

    assert [card.to_dict() for card in first] == [card.to_dict() for card in second]
