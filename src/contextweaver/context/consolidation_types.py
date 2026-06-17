"""Dataclasses and constants for the memory consolidation engine (issue #498).

The consolidation engine distills episodic memory into durable facts. These
pure-data types describe its configuration (:class:`ConsolidationPolicy`), its
intermediate clustering output (:class:`EpisodeCluster`), the promoted facts it
proposes (:class:`PromotedFact`), and the full run report
(:class:`ConsolidationReport`). All carry ``to_dict`` / ``from_dict`` for
lossless JSON round-trips, matching the repo serialization convention.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from contextweaver.exceptions import ConfigError
from contextweaver.types import Sensitivity

#: Schema version for :class:`ConsolidationReport` payloads.
CONSOLIDATION_REPORT_VERSION = "1"


@dataclass
class ConsolidationPolicy:
    """Thresholds and policy knobs for a consolidation run.

    Attributes:
        min_occurrences: Minimum number of clustered episodes required before a
            cluster is promoted to a durable fact.
        min_sessions: Minimum number of distinct sessions (counted from each
            episode's ``metadata[session_key]``) a cluster must span before it
            is promoted. Episodes without a session marker count as one shared
            "unscoped" session.
        similarity_threshold: Jaccard similarity (0..1) at or above which an
            episode joins an existing cluster.
        decay_after_days: Episodes / facts whose timestamp is older than this
            many days (relative to the run's ``as_of``) are reported as decayed.
            ``None`` disables decay reporting.
        timestamp_key: ``metadata`` key holding an ISO-8601 timestamp used for
            decay and for first/last-seen provenance.
        session_key: ``metadata`` key identifying the originating session, used
            for the ``min_sessions`` gate.
    """

    min_occurrences: int = 3
    min_sessions: int = 2
    similarity_threshold: float = 0.5
    decay_after_days: int | None = 90
    timestamp_key: str = "timestamp"
    session_key: str = "session_id"

    def validate(self) -> None:
        """Validate the policy values.

        Raises:
            ConfigError: If any threshold is out of range.
        """
        if self.min_occurrences < 1:
            raise ConfigError(f"min_occurrences must be >= 1, got {self.min_occurrences}")
        if self.min_sessions < 1:
            raise ConfigError(f"min_sessions must be >= 1, got {self.min_sessions}")
        if not 0.0 <= self.similarity_threshold <= 1.0:
            raise ConfigError(
                f"similarity_threshold must be in [0, 1], got {self.similarity_threshold}"
            )
        if self.decay_after_days is not None and self.decay_after_days < 0:
            raise ConfigError(f"decay_after_days must be >= 0 or None, got {self.decay_after_days}")

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "min_occurrences": self.min_occurrences,
            "min_sessions": self.min_sessions,
            "similarity_threshold": self.similarity_threshold,
            "decay_after_days": self.decay_after_days,
            "timestamp_key": self.timestamp_key,
            "session_key": self.session_key,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConsolidationPolicy:
        """Deserialise from a JSON-compatible dict produced by :meth:`to_dict`."""
        decay = data.get("decay_after_days", 90)
        return cls(
            min_occurrences=int(data.get("min_occurrences", 3)),
            min_sessions=int(data.get("min_sessions", 2)),
            similarity_threshold=float(data.get("similarity_threshold", 0.5)),
            decay_after_days=None if decay is None else int(decay),
            timestamp_key=str(data.get("timestamp_key", "timestamp")),
            session_key=str(data.get("session_key", "session_id")),
        )


@dataclass
class EpisodeCluster:
    """A deterministic grouping of similar episodes (issue #679).

    Attributes:
        cluster_id: Stable, zero-padded cluster identifier (``"cluster_000"``).
        episode_ids: Member episode IDs, sorted for determinism.
        canonical_text: Representative text for the cluster (the deterministic
            merge candidate before any optional model-assisted refinement).
    """

    cluster_id: str
    episode_ids: list[str] = field(default_factory=list)
    canonical_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "cluster_id": self.cluster_id,
            "episode_ids": list(self.episode_ids),
            "canonical_text": self.canonical_text,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EpisodeCluster:
        """Deserialise from a JSON-compatible dict produced by :meth:`to_dict`."""
        return cls(
            cluster_id=str(data["cluster_id"]),
            episode_ids=[str(e) for e in data.get("episode_ids", [])],
            canonical_text=str(data.get("canonical_text", "")),
        )


@dataclass
class PromotedFact:
    """A durable fact promoted from a cluster of episodes (issue #680).

    Every promoted fact carries complete provenance back to its source
    episodes so promotions are auditable and reversible.

    Attributes:
        fact_id: Deterministic, content-addressed fact ID (idempotent across
            re-runs over an unchanged store).
        key: Fact key under which the fact is stored (``"consolidated"``).
        text: Canonical fact text (post optional model-assisted merge).
        source_episode_ids: Source episode IDs, sorted.
        occurrences: Number of source episodes in the cluster.
        sessions: Number of distinct sessions the cluster spans.
        first_seen: Earliest source timestamp (ISO-8601), or ``None``.
        last_seen: Latest source timestamp (ISO-8601), or ``None``.
        sensitivity: Maximum sensitivity of the source episodes (inherited up,
            never down).
        merged_by_llm: ``True`` when an optional ``call_fn`` produced the text.
    """

    fact_id: str
    key: str
    text: str
    source_episode_ids: list[str] = field(default_factory=list)
    occurrences: int = 0
    sessions: int = 0
    first_seen: str | None = None
    last_seen: str | None = None
    sensitivity: Sensitivity = Sensitivity.public
    merged_by_llm: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "fact_id": self.fact_id,
            "key": self.key,
            "text": self.text,
            "source_episode_ids": list(self.source_episode_ids),
            "occurrences": self.occurrences,
            "sessions": self.sessions,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "sensitivity": self.sensitivity.value,
            "merged_by_llm": self.merged_by_llm,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PromotedFact:
        """Deserialise from a JSON-compatible dict produced by :meth:`to_dict`."""
        return cls(
            fact_id=str(data["fact_id"]),
            key=str(data.get("key", "consolidated")),
            text=str(data.get("text", "")),
            source_episode_ids=[str(e) for e in data.get("source_episode_ids", [])],
            occurrences=int(data.get("occurrences", 0)),
            sessions=int(data.get("sessions", 0)),
            first_seen=data.get("first_seen"),
            last_seen=data.get("last_seen"),
            sensitivity=Sensitivity(data.get("sensitivity", Sensitivity.public.value)),
            merged_by_llm=bool(data.get("merged_by_llm", False)),
        )


@dataclass
class ConsolidationReport:
    """Deterministic summary of a single consolidation run.

    Attributes:
        clusters: All episode clusters discovered.
        promoted: Facts promoted from clusters meeting the policy thresholds.
        decayed_episode_ids: Episode IDs past the decay horizon (report-only;
            never deleted — the stores are append-only).
        decayed_fact_ids: Fact IDs past the decay horizon (report-only).
        applied: ``True`` when promoted facts were written to the fact store.
        version: Schema version tag.
    """

    clusters: list[EpisodeCluster] = field(default_factory=list)
    promoted: list[PromotedFact] = field(default_factory=list)
    decayed_episode_ids: list[str] = field(default_factory=list)
    decayed_fact_ids: list[str] = field(default_factory=list)
    applied: bool = False
    version: str = CONSOLIDATION_REPORT_VERSION

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "version": self.version,
            "clusters": [c.to_dict() for c in self.clusters],
            "promoted": [p.to_dict() for p in self.promoted],
            "decayed_episode_ids": list(self.decayed_episode_ids),
            "decayed_fact_ids": list(self.decayed_fact_ids),
            "applied": self.applied,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConsolidationReport:
        """Deserialise from a JSON-compatible dict produced by :meth:`to_dict`."""
        return cls(
            clusters=[EpisodeCluster.from_dict(c) for c in data.get("clusters", [])],
            promoted=[PromotedFact.from_dict(p) for p in data.get("promoted", [])],
            decayed_episode_ids=[str(e) for e in data.get("decayed_episode_ids", [])],
            decayed_fact_ids=[str(f) for f in data.get("decayed_fact_ids", [])],
            applied=bool(data.get("applied", False)),
            version=str(data.get("version", CONSOLIDATION_REPORT_VERSION)),
        )

    def summary(self) -> str:
        """Return a compact, human-readable one-block summary."""
        return (
            f"Consolidation (v{self.version}): "
            f"clusters={len(self.clusters)} promoted={len(self.promoted)} "
            f"decayed_episodes={len(self.decayed_episode_ids)} "
            f"decayed_facts={len(self.decayed_fact_ids)} applied={self.applied}"
        )


__all__ = [
    "CONSOLIDATION_REPORT_VERSION",
    "ConsolidationPolicy",
    "ConsolidationReport",
    "EpisodeCluster",
    "PromotedFact",
]
