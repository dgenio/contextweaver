"""Memory consolidation engine (issue #498).

Distills episodic memory into durable, deduplicated, provenance-stamped facts:

1. :func:`cluster_episodes` — deterministic similarity clustering of episodes
   (issue #679).
2. :func:`promote_clusters` — promote clusters that meet the policy thresholds
   into :class:`~contextweaver.context.consolidation_types.PromotedFact` records
   with full source provenance and inherited (max) sensitivity (issue #680). An
   optional, fail-closed ``call_fn`` may refine the canonical text (issue #682).
3. :func:`decay_episodes` / :func:`decay_facts` — report entries past the decay
   horizon without ever deleting them (the stores are append-only; issue #681).
4. :func:`consolidate` — the orchestrator returning a
   :class:`~contextweaver.context.consolidation_types.ConsolidationReport`.

Everything is deterministic given identical store contents, policy, and
``as_of``: clustering iterates episodes in sorted-ID order, ties break by ID,
and promoted fact IDs are content-addressed so re-running ``apply=True`` over an
unchanged store is a no-op (idempotent upsert). Pure helper functions live in
:mod:`contextweaver.context._consolidation_helpers` to keep this module within
the size ceiling.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from contextweaver._utils import jaccard, tokenize
from contextweaver.context._consolidation_helpers import (
    CONSOLIDATED_FACT_KEY,
    canonical_fact_id,
    canonical_member,
    count_sessions,
    episode_iso,
    is_decayed,
    max_sensitivity,
    seen_bounds,
)
from contextweaver.context._consolidation_merge import refine_canonical_text
from contextweaver.context.consolidation_types import (
    ConsolidationPolicy,
    ConsolidationReport,
    EpisodeCluster,
    PromotedFact,
)
from contextweaver.protocols import EpisodicStore, FactStore
from contextweaver.store.episodic import Episode
from contextweaver.store.facts import Fact

logger = logging.getLogger("contextweaver.context")


def cluster_episodes(
    episodes: list[Episode],
    *,
    similarity_threshold: float = 0.5,
) -> list[EpisodeCluster]:
    """Group *episodes* into deterministic similarity clusters (issue #679).

    Episodes are processed in sorted-ID order. Each episode joins the first
    existing cluster whose seed summary has Jaccard similarity at or above
    *similarity_threshold*; otherwise it seeds a new cluster. The result is
    stable and idempotent for identical input.

    Args:
        episodes: Episodes to cluster.
        similarity_threshold: Jaccard similarity in ``[0, 1]`` for joining.

    Returns:
        Clusters in creation order, each with sorted ``episode_ids`` and a
        deterministic ``canonical_text``.
    """
    ordered = sorted(episodes, key=lambda ep: ep.episode_id)
    seeds: list[set[str]] = []
    buckets: list[list[Episode]] = []
    for ep in ordered:
        tokens = tokenize(ep.summary)
        placed = False
        for i, seed in enumerate(seeds):
            if jaccard(tokens, seed) >= similarity_threshold:
                buckets[i].append(ep)
                placed = True
                break
        if not placed:
            seeds.append(tokens)
            buckets.append([ep])

    clusters = [
        EpisodeCluster(
            cluster_id=f"cluster_{idx:03d}",
            episode_ids=sorted(ep.episode_id for ep in members),
            canonical_text=canonical_member(members),
        )
        for idx, members in enumerate(buckets)
    ]
    logger.debug("consolidation.cluster: episodes=%d clusters=%d", len(ordered), len(clusters))
    return clusters


def promote_clusters(
    clusters: list[EpisodeCluster],
    episodes_by_id: dict[str, Episode],
    policy: ConsolidationPolicy,
    *,
    call_fn: Callable[[str], str] | None = None,
    deterministic: bool = False,
) -> list[PromotedFact]:
    """Promote qualifying *clusters* into :class:`PromotedFact` records (#680).

    A cluster is promoted when it has at least ``policy.min_occurrences``
    episodes spanning at least ``policy.min_sessions`` distinct sessions. The
    promoted fact inherits the maximum source sensitivity and carries full
    provenance. When *call_fn* is supplied and *deterministic* is ``False``, the
    canonical text is refined under the fail-closed guardrails in
    :func:`~contextweaver.context._consolidation_merge.refine_canonical_text`.

    Args:
        clusters: Clusters from :func:`cluster_episodes`.
        episodes_by_id: Lookup from episode ID to :class:`Episode`.
        policy: Promotion thresholds.
        call_fn: Optional ``prompt -> completion`` callable for merge refinement.
        deterministic: When ``True``, ``call_fn`` is ignored (fail-closed).

    Returns:
        Promoted facts, ordered by ``fact_id`` for determinism.
    """
    promoted: list[PromotedFact] = []
    for cluster in clusters:
        members = [episodes_by_id[e] for e in cluster.episode_ids if e in episodes_by_id]
        if len(members) < policy.min_occurrences:
            continue
        sessions = count_sessions(members, policy.session_key)
        if sessions < policy.min_sessions:
            continue

        text = cluster.canonical_text
        merged_by_llm = False
        if call_fn is not None and not deterministic:
            text, merged_by_llm = refine_canonical_text(
                cluster.canonical_text,
                [ep.summary for ep in members],
                call_fn,
            )

        first_seen, last_seen = seen_bounds(members, policy.timestamp_key)
        promoted.append(
            PromotedFact(
                fact_id=canonical_fact_id(cluster.episode_ids),
                key=CONSOLIDATED_FACT_KEY,
                text=text,
                source_episode_ids=list(cluster.episode_ids),
                occurrences=len(members),
                sessions=sessions,
                first_seen=first_seen,
                last_seen=last_seen,
                sensitivity=max_sensitivity(members),
                merged_by_llm=merged_by_llm,
            )
        )
    promoted.sort(key=lambda pf: pf.fact_id)
    return promoted


def decay_episodes(
    episodes: list[Episode],
    policy: ConsolidationPolicy,
    *,
    as_of: datetime,
) -> list[str]:
    """Return IDs of *episodes* past the decay horizon (report-only; #681).

    Decay never deletes — the episodic store is append-only. Callers decide how
    to act on the returned IDs (e.g. status tombstones in their own backend).
    """
    if policy.decay_after_days is None:
        return []
    return sorted(
        ep.episode_id
        for ep in episodes
        if is_decayed(episode_iso(ep, policy.timestamp_key), as_of, policy.decay_after_days)
    )


def decay_facts(
    facts: list[Fact],
    policy: ConsolidationPolicy,
    *,
    as_of: datetime,
) -> list[str]:
    """Return IDs of *facts* past the decay horizon (report-only; #681)."""
    if policy.decay_after_days is None:
        return []
    stale: list[str] = []
    for fact in facts:
        value = fact.metadata.get(policy.timestamp_key)
        iso = value if isinstance(value, str) else None
        if is_decayed(iso, as_of, policy.decay_after_days):
            stale.append(fact.fact_id)
    return sorted(stale)


def consolidate(
    episodic_store: EpisodicStore,
    fact_store: FactStore,
    policy: ConsolidationPolicy | None = None,
    *,
    as_of: datetime | None = None,
    call_fn: Callable[[str], str] | None = None,
    deterministic: bool = False,
    apply: bool = False,
) -> ConsolidationReport:
    """Run the consolidation pipeline over *episodic_store* (issue #498).

    Args:
        episodic_store: Source of episodes to consolidate.
        fact_store: Target fact store (written only when *apply* is ``True``).
        policy: Thresholds; defaults to :class:`ConsolidationPolicy`.
        as_of: Reference time for decay reporting. When ``None``, no decay is
            reported.
        call_fn: Optional ``prompt -> completion`` callable for merge refinement.
        deterministic: When ``True``, ``call_fn`` is ignored (fail-closed).
        apply: When ``True``, promoted facts are upserted into *fact_store* with
            provenance metadata. Idempotent: re-running over an unchanged store
            rewrites identical facts.

    Returns:
        A :class:`ConsolidationReport`.

    Raises:
        ConfigError: If *policy* fails validation.
    """
    policy = policy if policy is not None else ConsolidationPolicy()
    policy.validate()

    episodes = episodic_store.all()
    episodes_by_id = {ep.episode_id: ep for ep in episodes}
    clusters = cluster_episodes(episodes, similarity_threshold=policy.similarity_threshold)
    promoted = promote_clusters(
        clusters, episodes_by_id, policy, call_fn=call_fn, deterministic=deterministic
    )

    decayed_episode_ids = decay_episodes(episodes, policy, as_of=as_of) if as_of else []
    decayed_fact_ids = decay_facts(fact_store.all(), policy, as_of=as_of) if as_of else []

    if apply:
        for pf in promoted:
            metadata: dict[str, object] = {
                "consolidated": True,
                "source_episode_ids": list(pf.source_episode_ids),
                "occurrences": pf.occurrences,
                "sessions": pf.sessions,
                "first_seen": pf.first_seen,
                "last_seen": pf.last_seen,
                "merged_by_llm": pf.merged_by_llm,
            }
            # Stamp the policy's decay timestamp key with the fact's recency
            # (its last-seen source time) so the promoted fact is itself
            # eligible for decay reporting on later runs, not just its episodes.
            if pf.last_seen is not None:
                metadata[policy.timestamp_key] = pf.last_seen
            fact_store.put(
                Fact(
                    fact_id=pf.fact_id,
                    key=pf.key,
                    value=pf.text,
                    metadata=metadata,
                    sensitivity=pf.sensitivity,
                )
            )

    report = ConsolidationReport(
        clusters=clusters,
        promoted=promoted,
        decayed_episode_ids=decayed_episode_ids,
        decayed_fact_ids=decayed_fact_ids,
        applied=apply,
    )
    logger.debug("consolidation.run: %s", report.summary())
    return report


__all__ = [
    "CONSOLIDATED_FACT_KEY",
    "cluster_episodes",
    "consolidate",
    "decay_episodes",
    "decay_facts",
    "promote_clusters",
]
