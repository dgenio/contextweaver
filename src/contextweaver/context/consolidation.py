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
unchanged store is a no-op (idempotent upsert).
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from contextweaver._utils import jaccard, tokenize
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
from contextweaver.types import Sensitivity

logger = logging.getLogger("contextweaver.context")

#: Fact key under which consolidated facts are stored.
CONSOLIDATED_FACT_KEY = "consolidated"

#: Severity ranking used to inherit the maximum sensitivity of source episodes.
_SENSITIVITY_RANK: dict[Sensitivity, int] = {
    Sensitivity.public: 0,
    Sensitivity.internal: 1,
    Sensitivity.confidential: 2,
    Sensitivity.restricted: 3,
}


def canonical_member(members: list[Episode]) -> str:
    """Return the deterministic representative summary for *members*.

    Picks the summary with the most tokens (most informative), breaking ties by
    the smallest ``episode_id`` so the choice is reproducible.
    """
    best = min(members, key=lambda ep: (-len(tokenize(ep.summary)), ep.episode_id))
    return best.summary


def max_sensitivity(members: list[Episode]) -> Sensitivity:
    """Return the highest sensitivity among *members* (defaults to public)."""
    return max(
        (ep.sensitivity for ep in members),
        key=lambda s: _SENSITIVITY_RANK[s],
        default=Sensitivity.public,
    )


def count_sessions(members: list[Episode], session_key: str) -> int:
    """Count distinct sessions in *members*.

    Episodes lacking a session marker collectively count as one shared session.
    """
    sessions: set[str] = set()
    for ep in members:
        value = ep.metadata.get(session_key)
        sessions.add(str(value) if value is not None else "\x00unscoped")
    return len(sessions)


def episode_iso(ep: Episode, key: str) -> str | None:
    """Return *ep*'s ISO-8601 timestamp metadata value, or ``None``."""
    value = ep.metadata.get(key)
    return value if isinstance(value, str) and value else None


def coerce_iso(value: object) -> str | None:
    """Coerce a metadata timestamp *value* to ISO text, or ``None``."""
    if isinstance(value, datetime):
        return value.isoformat()
    return value if isinstance(value, str) and value else None


def _to_naive_utc(dt: datetime) -> datetime:
    """Return *dt* as a naive UTC datetime (tz-aware inputs are converted)."""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO-8601 *value* to a naive-UTC ``datetime``, or ``None``."""
    if not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        return _to_naive_utc(datetime.fromisoformat(text))
    except ValueError:
        return None


def seen_bounds(members: list[Episode], key: str) -> tuple[str | None, str | None]:
    """Return the (first_seen, last_seen) ISO timestamps across *members*."""
    stamped = [(iso, parse_iso(iso)) for ep in members if (iso := episode_iso(ep, key))]
    parsed = [(iso, dt) for iso, dt in stamped if dt is not None]
    if not parsed:
        return None, None
    first = min(parsed, key=lambda pair: pair[1])[0]
    last = max(parsed, key=lambda pair: pair[1])[0]
    return first, last


def canonical_fact_id(source_ids: list[str]) -> str:
    """Return a deterministic, content-addressed fact ID for *source_ids*."""
    digest = hashlib.sha1("\n".join(sorted(source_ids)).encode("utf-8")).hexdigest()[:12]
    return f"fact:{CONSOLIDATED_FACT_KEY}:{digest}"


def is_decayed(iso: str | None, as_of: datetime, decay_after_days: int) -> bool:
    """Return ``True`` when *iso* is older than *decay_after_days* before *as_of*."""
    stamp = parse_iso(iso)
    if stamp is None:
        return False
    return _to_naive_utc(as_of) - stamp > timedelta(days=decay_after_days)


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
    """Promote qualifying *clusters* into :class:`PromotedFact` records (#680)."""
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
    """Return IDs of *episodes* past the decay horizon (report-only; #681)."""
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
        iso = coerce_iso(fact.metadata.get(policy.timestamp_key))
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
    """Run the consolidation pipeline over *episodic_store* (issue #498)."""
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
    "parse_iso",
    "promote_clusters",
]
