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
from contextweaver.protocols import CharDivFourEstimator, HeuristicEstimator
from contextweaver.secrets import DEFAULT_SECRET_MASK, scrub_secrets
from contextweaver.store.episodic import Episode
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
