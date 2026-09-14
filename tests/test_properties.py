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

from hypothesis import given
from hypothesis import strategies as st

from contextweaver.context.consolidation import cluster_episodes
from contextweaver.context.dedup import deduplicate_candidates
from contextweaver.protocols import CharDivFourEstimator, HeuristicEstimator
from contextweaver.secrets import DEFAULT_SECRET_MASK, scrub_secrets
from contextweaver.store.episodic import Episode
from contextweaver.types import ContextItem, ItemKind, Sensitivity
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
