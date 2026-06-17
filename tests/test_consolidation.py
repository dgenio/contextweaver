"""Tests for the memory consolidation engine (issues #498, #679-#682)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from contextweaver.context.consolidation import (
    cluster_episodes,
    consolidate,
    decay_episodes,
    promote_clusters,
)
from contextweaver.context.consolidation_types import (
    CONSOLIDATION_REPORT_VERSION,
    ConsolidationPolicy,
    ConsolidationReport,
    EpisodeCluster,
    PromotedFact,
)
from contextweaver.exceptions import ConfigError
from contextweaver.store.episodic import Episode, InMemoryEpisodicStore
from contextweaver.store.facts import InMemoryFactStore
from contextweaver.types import Sensitivity

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ep(
    eid: str,
    summary: str,
    *,
    session: str | None = None,
    ts: str | None = None,
    sensitivity: Sensitivity = Sensitivity.public,
) -> Episode:
    metadata: dict[str, object] = {}
    if session is not None:
        metadata["session_id"] = session
    if ts is not None:
        metadata["timestamp"] = ts
    return Episode(episode_id=eid, summary=summary, metadata=metadata, sensitivity=sensitivity)


def _store(*episodes: Episode) -> InMemoryEpisodicStore:
    store = InMemoryEpisodicStore()
    for ep in episodes:
        store.add(ep)
    return store


_EMAIL = "customer prefers email contact for support"


# ---------------------------------------------------------------------------
# Clustering (#679)
# ---------------------------------------------------------------------------


def test_cluster_groups_similar_and_separates_dissimilar() -> None:
    episodes = [
        _ep("a", "customer prefers email contact for support"),
        _ep("b", "customer prefers email contact when reaching support"),
        _ep("c", "the build failed due to a missing dependency"),
    ]
    clusters = cluster_episodes(episodes, similarity_threshold=0.4)
    assert len(clusters) == 2
    # Email episodes cluster together; the build episode is its own cluster.
    grouped = {c.cluster_id: c.episode_ids for c in clusters}
    assert grouped["cluster_000"] == ["a", "b"]
    assert grouped["cluster_001"] == ["c"]


def test_cluster_is_deterministic_and_idempotent() -> None:
    episodes = [
        _ep("z", "customer prefers email contact for support"),
        _ep("a", "customer prefers email contact for support today"),
        _ep("m", "deploy pipeline timed out on staging"),
    ]
    first = cluster_episodes(episodes, similarity_threshold=0.4)
    second = cluster_episodes(list(reversed(episodes)), similarity_threshold=0.4)
    assert [c.to_dict() for c in first] == [c.to_dict() for c in second]


def test_cluster_canonical_text_picks_most_informative() -> None:
    episodes = [
        _ep("a", "email preferred"),
        _ep("b", "customer prefers email contact for support tickets"),
    ]
    [cluster] = cluster_episodes(episodes, similarity_threshold=0.1)
    assert cluster.canonical_text == "customer prefers email contact for support tickets"


def test_cluster_empty() -> None:
    assert cluster_episodes([], similarity_threshold=0.5) == []


# ---------------------------------------------------------------------------
# Promotion (#680)
# ---------------------------------------------------------------------------


def test_promote_requires_min_occurrences_and_sessions() -> None:
    episodes = [
        _ep("a", _EMAIL, session="s1"),
        _ep("b", _EMAIL, session="s2"),
        _ep("c", _EMAIL, session="s3"),
    ]
    by_id = {e.episode_id: e for e in episodes}
    clusters = cluster_episodes(episodes, similarity_threshold=0.4)
    policy = ConsolidationPolicy(min_occurrences=3, min_sessions=2)
    [fact] = promote_clusters(clusters, by_id, policy)
    assert fact.occurrences == 3
    assert fact.sessions == 3
    assert fact.source_episode_ids == ["a", "b", "c"]
    assert fact.key == "consolidated"


def test_promote_blocked_below_occurrence_threshold() -> None:
    episodes = [_ep("a", _EMAIL, session="s1"), _ep("b", _EMAIL, session="s2")]
    by_id = {e.episode_id: e for e in episodes}
    clusters = cluster_episodes(episodes, similarity_threshold=0.4)
    policy = ConsolidationPolicy(min_occurrences=3, min_sessions=2)
    assert promote_clusters(clusters, by_id, policy) == []


def test_promote_blocked_below_session_threshold() -> None:
    # Three occurrences but all from the same session.
    episodes = [_ep(e, _EMAIL, session="s1") for e in ("a", "b", "c")]
    by_id = {e.episode_id: e for e in episodes}
    clusters = cluster_episodes(episodes, similarity_threshold=0.4)
    policy = ConsolidationPolicy(min_occurrences=3, min_sessions=2)
    assert promote_clusters(clusters, by_id, policy) == []


def test_promote_inherits_max_sensitivity() -> None:
    episodes = [
        _ep("a", _EMAIL, session="s1", sensitivity=Sensitivity.public),
        _ep("b", _EMAIL, session="s2", sensitivity=Sensitivity.confidential),
        _ep("c", _EMAIL, session="s3", sensitivity=Sensitivity.internal),
    ]
    by_id = {e.episode_id: e for e in episodes}
    clusters = cluster_episodes(episodes, similarity_threshold=0.4)
    [fact] = promote_clusters(
        clusters, by_id, ConsolidationPolicy(min_occurrences=3, min_sessions=2)
    )
    assert fact.sensitivity is Sensitivity.confidential


def test_promote_records_seen_bounds() -> None:
    episodes = [
        _ep("a", _EMAIL, session="s1", ts="2026-01-05T00:00:00"),
        _ep("b", _EMAIL, session="s2", ts="2026-01-01T00:00:00"),
        _ep("c", _EMAIL, session="s3", ts="2026-03-09T00:00:00"),
    ]
    by_id = {e.episode_id: e for e in episodes}
    clusters = cluster_episodes(episodes, similarity_threshold=0.4)
    [fact] = promote_clusters(
        clusters, by_id, ConsolidationPolicy(min_occurrences=3, min_sessions=2)
    )
    assert fact.first_seen == "2026-01-01T00:00:00"
    assert fact.last_seen == "2026-03-09T00:00:00"


def test_promote_fact_id_is_content_addressed_and_stable() -> None:
    episodes = [_ep(e, _EMAIL, session=f"s{i}") for i, e in enumerate(("a", "b", "c"))]
    by_id = {e.episode_id: e for e in episodes}
    clusters = cluster_episodes(episodes, similarity_threshold=0.4)
    pol = ConsolidationPolicy(min_occurrences=3, min_sessions=2)
    first = promote_clusters(clusters, by_id, pol)[0].fact_id
    second = promote_clusters(clusters, by_id, pol)[0].fact_id
    assert first == second
    assert first.startswith("fact:consolidated:")


# ---------------------------------------------------------------------------
# Optional LLM merge (#682)
# ---------------------------------------------------------------------------


def _grounded_call(_prompt: str) -> str:
    # Reorders grounded tokens only — no new entity introduced.
    return "support email contact customer prefers for"


def _hallucinating_call(_prompt: str) -> str:
    return "customer prefers carrier pigeons"


def test_llm_merge_accepts_grounded_completion() -> None:
    episodes = [_ep(e, _EMAIL, session=f"s{i}") for i, e in enumerate(("a", "b", "c"))]
    by_id = {e.episode_id: e for e in episodes}
    clusters = cluster_episodes(episodes, similarity_threshold=0.4)
    [fact] = promote_clusters(
        clusters,
        by_id,
        ConsolidationPolicy(min_occurrences=3, min_sessions=2),
        call_fn=_grounded_call,
    )
    assert fact.merged_by_llm is True
    assert fact.text == "support email contact customer prefers for"


def test_llm_merge_rejects_ungrounded_completion() -> None:
    episodes = [_ep(e, _EMAIL, session=f"s{i}") for i, e in enumerate(("a", "b", "c"))]
    by_id = {e.episode_id: e for e in episodes}
    clusters = cluster_episodes(episodes, similarity_threshold=0.4)
    [fact] = promote_clusters(
        clusters,
        by_id,
        ConsolidationPolicy(min_occurrences=3, min_sessions=2),
        call_fn=_hallucinating_call,
    )
    assert fact.merged_by_llm is False
    assert fact.text == clusters[0].canonical_text


def test_llm_merge_disabled_under_deterministic() -> None:
    episodes = [_ep(e, _EMAIL, session=f"s{i}") for i, e in enumerate(("a", "b", "c"))]
    by_id = {e.episode_id: e for e in episodes}
    clusters = cluster_episodes(episodes, similarity_threshold=0.4)
    [fact] = promote_clusters(
        clusters,
        by_id,
        ConsolidationPolicy(min_occurrences=3, min_sessions=2),
        call_fn=_grounded_call,
        deterministic=True,
    )
    assert fact.merged_by_llm is False


# ---------------------------------------------------------------------------
# Decay (#681)
# ---------------------------------------------------------------------------


def test_decay_boundary_is_strict() -> None:
    base = datetime(2026, 1, 1, 0, 0, 0)
    episodes = [_ep("a", "stale note", ts=base.isoformat())]
    policy = ConsolidationPolicy(decay_after_days=90)
    # Exactly 90 days later: not yet decayed (strictly greater-than).
    assert decay_episodes(episodes, policy, as_of=base + timedelta(days=90)) == []
    # 91 days later: decayed.
    assert decay_episodes(episodes, policy, as_of=base + timedelta(days=91)) == ["a"]


def test_decay_disabled_when_none() -> None:
    base = datetime(2026, 1, 1)
    episodes = [_ep("a", "stale", ts=base.isoformat())]
    policy = ConsolidationPolicy(decay_after_days=None)
    assert decay_episodes(episodes, policy, as_of=base + timedelta(days=9999)) == []


def test_decay_ignores_episodes_without_timestamp() -> None:
    episodes = [_ep("a", "no timestamp")]
    policy = ConsolidationPolicy(decay_after_days=1)
    assert decay_episodes(episodes, policy, as_of=datetime(2030, 1, 1)) == []


def test_decay_never_mutates_store() -> None:
    base = datetime(2026, 1, 1)
    store = _store(_ep("a", _EMAIL, session="s1", ts=base.isoformat()))
    facts = InMemoryFactStore()
    report = consolidate(store, facts, as_of=base + timedelta(days=400))
    assert report.decayed_episode_ids == ["a"]
    # Append-only invariant: the episode is still present after a decay report.
    assert store.get("a") is not None


# ---------------------------------------------------------------------------
# Orchestration / apply (#498)
# ---------------------------------------------------------------------------


def test_consolidate_apply_writes_facts_with_provenance() -> None:
    store = _store(
        _ep("a", _EMAIL, session="s1"),
        _ep("b", _EMAIL, session="s2"),
        _ep("c", _EMAIL, session="s3"),
    )
    facts = InMemoryFactStore()
    report = consolidate(
        store, facts, ConsolidationPolicy(min_occurrences=3, min_sessions=2), apply=True
    )
    assert report.applied is True
    stored = facts.all()
    assert len(stored) == 1
    fact = stored[0]
    assert fact.metadata["consolidated"] is True
    assert fact.metadata["source_episode_ids"] == ["a", "b", "c"]
    assert fact.metadata["occurrences"] == 3


def test_consolidate_apply_is_idempotent() -> None:
    store = _store(*[_ep(e, _EMAIL, session=f"s{i}") for i, e in enumerate(("a", "b", "c"))])
    facts = InMemoryFactStore()
    pol = ConsolidationPolicy(min_occurrences=3, min_sessions=2)
    consolidate(store, facts, pol, apply=True)
    consolidate(store, facts, pol, apply=True)
    assert len(facts.all()) == 1


def test_consolidate_no_apply_leaves_fact_store_untouched() -> None:
    store = _store(*[_ep(e, _EMAIL, session=f"s{i}") for i, e in enumerate(("a", "b", "c"))])
    facts = InMemoryFactStore()
    report = consolidate(store, facts, ConsolidationPolicy(min_occurrences=3, min_sessions=2))
    assert report.applied is False
    assert facts.all() == []
    assert len(report.promoted) == 1


def test_consolidate_empty_store() -> None:
    report = consolidate(InMemoryEpisodicStore(), InMemoryFactStore())
    assert report.clusters == []
    assert report.promoted == []


def test_consolidate_invalid_policy_raises() -> None:
    with pytest.raises(ConfigError):
        consolidate(
            InMemoryEpisodicStore(),
            InMemoryFactStore(),
            ConsolidationPolicy(similarity_threshold=1.5),
        )


# ---------------------------------------------------------------------------
# Serialization round-trips
# ---------------------------------------------------------------------------


def test_report_round_trip() -> None:
    store = _store(*[_ep(e, _EMAIL, session=f"s{i}") for i, e in enumerate(("a", "b", "c"))])
    report = consolidate(
        store, InMemoryFactStore(), ConsolidationPolicy(min_occurrences=3, min_sessions=2)
    )
    restored = ConsolidationReport.from_dict(report.to_dict())
    assert restored.to_dict() == report.to_dict()
    assert restored.version == CONSOLIDATION_REPORT_VERSION


def test_policy_round_trip_preserves_none_decay() -> None:
    policy = ConsolidationPolicy(decay_after_days=None, min_occurrences=5)
    restored = ConsolidationPolicy.from_dict(policy.to_dict())
    assert restored == policy


def test_dataclass_round_trips() -> None:
    cluster = EpisodeCluster(cluster_id="cluster_000", episode_ids=["a"], canonical_text="x")
    assert EpisodeCluster.from_dict(cluster.to_dict()) == cluster
    fact = PromotedFact(
        fact_id="fact:consolidated:abc",
        key="consolidated",
        text="x",
        source_episode_ids=["a"],
        occurrences=1,
        sessions=1,
        sensitivity=Sensitivity.internal,
    )
    assert PromotedFact.from_dict(fact.to_dict()) == fact


# ---------------------------------------------------------------------------
# Timezone / Z-suffix handling + sub-day decay (review fixes)
# ---------------------------------------------------------------------------


def test_decay_handles_z_suffix_and_tz_aware_as_of() -> None:
    # RFC 3339 'Z' on the episode + a tz-aware as_of: neither should raise, and
    # the stale episode is still reported.
    episodes = [_ep("a", "stale note", ts="2026-01-01T00:00:00Z")]
    policy = ConsolidationPolicy(decay_after_days=90)
    as_of = datetime.fromisoformat("2026-06-01T00:00:00+00:00")
    assert decay_episodes(episodes, policy, as_of=as_of) == ["a"]


def test_decay_sub_day_granularity() -> None:
    base = datetime(2026, 1, 1, 0, 0, 0)
    episodes = [_ep("a", "stale", ts=base.isoformat())]
    policy = ConsolidationPolicy(decay_after_days=90)
    # 90 days + 23h is past the horizon (timedelta comparison, not floored days).
    assert decay_episodes(episodes, policy, as_of=base + timedelta(days=90, hours=23)) == ["a"]


def test_seen_bounds_preserves_original_z_strings() -> None:
    episodes = [
        _ep("a", _EMAIL, session="s1", ts="2026-01-05T00:00:00Z"),
        _ep("b", _EMAIL, session="s2", ts="2026-01-01T00:00:00Z"),
        _ep("c", _EMAIL, session="s3", ts="2026-03-09T00:00:00Z"),
    ]
    by_id = {e.episode_id: e for e in episodes}
    clusters = cluster_episodes(episodes, similarity_threshold=0.4)
    [fact] = promote_clusters(
        clusters, by_id, ConsolidationPolicy(min_occurrences=3, min_sessions=2)
    )
    assert fact.first_seen == "2026-01-01T00:00:00Z"
    assert fact.last_seen == "2026-03-09T00:00:00Z"


def test_consolidated_fact_is_decayable_on_later_run() -> None:
    base = datetime(2026, 1, 1)
    store = _store(
        *[
            _ep(e, _EMAIL, session=f"s{i}", ts=base.isoformat())
            for i, e in enumerate(("a", "b", "c"))
        ]
    )
    facts = InMemoryFactStore()
    pol = ConsolidationPolicy(min_occurrences=3, min_sessions=2, decay_after_days=90)
    consolidate(store, facts, pol, apply=True)
    stored = facts.all()[0]
    # The promoted fact carries the policy decay key, so a later run reports it.
    assert stored.metadata[pol.timestamp_key] == base.isoformat()
    later = consolidate(store, facts, pol, as_of=base + timedelta(days=400))
    assert stored.fact_id in later.decayed_fact_ids
