"""Feedback-aware routing score extension point (issue #318).

contextweaver routes deterministically by default.  This module adds an
*opt-in* seam so callers can fold historical execution signals — success
rate, latency, token cost, result quality — into the routing score without
giving up determinism or coupling the library to any external router.

The pieces: :class:`ExecutionFeedback` (a contextweaver-native record of one
past execution — **not** a weaver-spec contract type; the spec defines none),
:class:`~contextweaver.protocols.RoutingScoreProvider` (the plug-point),
:class:`DeterministicScoreProvider` (no-op default, byte-equivalent to passing
no provider), :class:`FeedbackAwareScoreProvider` (bounded feedback deltas),
and :func:`aggregate_feedback` (folds a history list into one record per id).

Determinism guarantee: every provider re-sorts by ``(-score, item_id)``, so
ties always break by ascending id — identical to the rest of the engine.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from contextweaver.exceptions import ConfigError

# Default scoring weights.  Kept small so feedback *nudges* the base
# retrieval score rather than overriding it; callers tune via the
# :class:`FeedbackAwareScoreProvider` constructor.
DEFAULT_SUCCESS_WEIGHT: float = 0.1
DEFAULT_FAILURE_PENALTY: float = 0.2
DEFAULT_QUALITY_WEIGHT: float = 0.1
DEFAULT_LATENCY_WEIGHT: float = 0.0
DEFAULT_COST_WEIGHT: float = 0.0
DEFAULT_LATENCY_REF_MS: float = 1000.0
DEFAULT_TOKEN_COST_REF: float = 1000.0


def _clamp01(value: float) -> float:
    """Clamp *value* into the closed unit interval ``[0.0, 1.0]``."""
    return max(0.0, min(1.0, value))


def _resort(scored: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Return *scored* sorted by descending score, ties broken by ascending id."""
    return sorted(scored, key=lambda pair: (-pair[1], pair[0]))


@dataclass
class ExecutionFeedback:
    """A single historical execution signal for a routable item (issue #318).

    Attributes:
        item_id: The :class:`~contextweaver.types.SelectableItem` id this
            feedback describes.
        success: Whether the execution succeeded.  Defaults to ``True``.
        latency_ms: Optional wall-clock latency in milliseconds.
        token_cost: Optional token cost of the execution.
        quality_score: Optional result-quality signal in ``[0.0, 1.0]``
            (e.g. a rubric score or thumbs-up rate).
        timestamp: Optional timezone-aware time the execution occurred.
        metadata: Free-form provenance.  :func:`aggregate_feedback` writes
            ``sample_count`` and ``success_rate`` here.
    """

    item_id: str
    success: bool = True
    latency_ms: float | None = None
    token_cost: int | None = None
    quality_score: float | None = None
    timestamp: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict.

        ``timestamp`` is emitted as an ISO-8601 string (or ``None``);
        all other optional fields round-trip as themselves.
        """
        return {
            "item_id": self.item_id,
            "success": self.success,
            "latency_ms": self.latency_ms,
            "token_cost": self.token_cost,
            "quality_score": self.quality_score,
            "timestamp": self.timestamp.isoformat() if self.timestamp is not None else None,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionFeedback:
        """Deserialise from a dict previously produced by :meth:`to_dict`.

        Missing optional keys fall back to the dataclass defaults so older
        payloads round-trip cleanly.
        """
        raw_ts = data.get("timestamp")
        timestamp: datetime | None = None
        if isinstance(raw_ts, str) and raw_ts:
            timestamp = datetime.fromisoformat(raw_ts)
            # Match RoutingDecision/Frame handling: coerce naive timestamps to
            # UTC so a restored ``ExecutionFeedback`` is always tz-aware.
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
        raw_cost = data.get("token_cost")
        raw_quality = data.get("quality_score")
        raw_latency = data.get("latency_ms")
        return cls(
            item_id=data["item_id"],
            success=bool(data.get("success", True)),
            latency_ms=float(raw_latency) if raw_latency is not None else None,
            token_cost=int(raw_cost) if raw_cost is not None else None,
            quality_score=float(raw_quality) if raw_quality is not None else None,
            timestamp=timestamp,
            metadata=dict(data.get("metadata", {})),
        )


def aggregate_feedback(entries: Iterable[ExecutionFeedback]) -> dict[str, ExecutionFeedback]:
    """Fold a history of :class:`ExecutionFeedback` into one record per item.

    Numeric signals (``latency_ms``, ``token_cost``, ``quality_score``) are
    averaged over the entries that supplied them; ``success`` becomes the
    majority vote (``success_rate >= 0.5``); ``timestamp`` is the most recent
    non-null value.  The aggregated record's ``metadata`` carries
    ``sample_count`` and the exact ``success_rate`` so callers keep the raw
    signal.  Deterministic: item ids are processed in sorted order and the
    arithmetic does not depend on input ordering.

    Args:
        entries: Historical feedback records, in any order.

    Returns:
        A mapping ``item_id -> ExecutionFeedback`` suitable for
        :class:`FeedbackAwareScoreProvider`.
    """
    grouped: dict[str, list[ExecutionFeedback]] = {}
    for entry in entries:
        grouped.setdefault(entry.item_id, []).append(entry)

    aggregated: dict[str, ExecutionFeedback] = {}
    for item_id in sorted(grouped):
        records = grouped[item_id]
        count = len(records)
        success_rate = sum(1 for r in records if r.success) / count
        latencies = [r.latency_ms for r in records if r.latency_ms is not None]
        costs = [r.token_cost for r in records if r.token_cost is not None]
        qualities = [r.quality_score for r in records if r.quality_score is not None]
        timestamps = [r.timestamp for r in records if r.timestamp is not None]
        aggregated[item_id] = ExecutionFeedback(
            item_id=item_id,
            success=success_rate >= 0.5,
            latency_ms=(sum(latencies) / len(latencies)) if latencies else None,
            token_cost=round(sum(costs) / len(costs)) if costs else None,
            quality_score=(sum(qualities) / len(qualities)) if qualities else None,
            timestamp=max(timestamps) if timestamps else None,
            metadata={"sample_count": count, "success_rate": success_rate},
        )
    return aggregated


class DeterministicScoreProvider:
    """The default :class:`~contextweaver.protocols.RoutingScoreProvider`.

    Returns the candidates unchanged apart from enforcing the canonical
    ``(-score, id)`` ordering.  Passing this provider to
    :class:`~contextweaver.routing.router.Router` is byte-equivalent to
    passing none — it exists so callers can be explicit, and as a base other
    providers can fall back to.
    """

    def adjust(self, query: str, scored: list[tuple[str, float]]) -> list[tuple[str, float]]:
        """Return *scored* in canonical deterministic order (no score change)."""
        return _resort(scored)


class FeedbackAwareScoreProvider:
    """A :class:`~contextweaver.protocols.RoutingScoreProvider` that folds in feedback.

    For every candidate that has an :class:`ExecutionFeedback` entry, a
    bounded delta is added to the base retrieval score::

        delta  = +success_weight        if feedback.success else -failure_penalty
        delta += quality_weight  * (2 * clamp01(quality_score) - 1)   # in [-w, +w]
        delta -= latency_weight  * clamp01(latency_ms / latency_ref_ms)   # if weight > 0
        delta -= cost_weight     * clamp01(token_cost / token_cost_ref)   # if weight > 0

    The latency/cost ratios are clamped to ``[0, 1]`` so every term is bounded
    by its weight — an outlier can never swamp the base retrieval score.

    Candidates with no feedback keep their base score.  Results are re-sorted
    by ``(-score, id)`` so ties still break by ascending id — feedback never
    introduces nondeterminism.

    Args:
        feedback: Either a mapping ``item_id -> ExecutionFeedback`` (one
            aggregated record per item) or a flat sequence of feedback
            records, which is aggregated via :func:`aggregate_feedback`.
        success_weight: Bonus added when ``feedback.success`` is ``True``.
        failure_penalty: Penalty subtracted when ``feedback.success`` is
            ``False``.
        quality_weight: Scales the ``quality_score`` contribution.
        latency_weight: Scales the latency penalty.  ``0.0`` (default)
            disables it.
        cost_weight: Scales the token-cost penalty.  ``0.0`` (default)
            disables it.
        latency_ref_ms: Latency that maps to a full ``latency_weight``
            penalty.  Must be ``> 0``.
        token_cost_ref: Token cost that maps to a full ``cost_weight``
            penalty.  Must be ``> 0``.

    Raises:
        ConfigError: If any weight is negative or a reference value is not
            strictly positive.
    """

    def __init__(
        self,
        feedback: Mapping[str, ExecutionFeedback] | Sequence[ExecutionFeedback],
        *,
        success_weight: float = DEFAULT_SUCCESS_WEIGHT,
        failure_penalty: float = DEFAULT_FAILURE_PENALTY,
        quality_weight: float = DEFAULT_QUALITY_WEIGHT,
        latency_weight: float = DEFAULT_LATENCY_WEIGHT,
        cost_weight: float = DEFAULT_COST_WEIGHT,
        latency_ref_ms: float = DEFAULT_LATENCY_REF_MS,
        token_cost_ref: float = DEFAULT_TOKEN_COST_REF,
    ) -> None:
        for name, value in (
            ("success_weight", success_weight),
            ("failure_penalty", failure_penalty),
            ("quality_weight", quality_weight),
            ("latency_weight", latency_weight),
            ("cost_weight", cost_weight),
        ):
            if value < 0:
                raise ConfigError(f"{name} must be >= 0, got {value}")
        if latency_ref_ms <= 0:
            raise ConfigError(f"latency_ref_ms must be > 0, got {latency_ref_ms}")
        if token_cost_ref <= 0:
            raise ConfigError(f"token_cost_ref must be > 0, got {token_cost_ref}")
        if isinstance(feedback, Mapping):
            self._feedback: dict[str, ExecutionFeedback] = dict(feedback)
        else:
            self._feedback = aggregate_feedback(feedback)
        self._success_weight = success_weight
        self._failure_penalty = failure_penalty
        self._quality_weight = quality_weight
        self._latency_weight = latency_weight
        self._cost_weight = cost_weight
        self._latency_ref_ms = latency_ref_ms
        self._token_cost_ref = token_cost_ref

    def _delta(self, feedback: ExecutionFeedback) -> float:
        """Compute the bounded score delta for one feedback record."""
        delta = self._success_weight if feedback.success else -self._failure_penalty
        if feedback.quality_score is not None:
            delta += self._quality_weight * (2.0 * _clamp01(feedback.quality_score) - 1.0)
        # Clamp the latency/cost ratios to [0, 1] so each penalty is bounded by
        # its weight; an outlier latency/cost can never swamp the base score.
        if self._latency_weight > 0 and feedback.latency_ms is not None:
            delta -= self._latency_weight * _clamp01(feedback.latency_ms / self._latency_ref_ms)
        if self._cost_weight > 0 and feedback.token_cost is not None:
            delta -= self._cost_weight * _clamp01(feedback.token_cost / self._token_cost_ref)
        return delta

    def adjust(self, query: str, scored: list[tuple[str, float]]) -> list[tuple[str, float]]:
        """Apply feedback deltas to *scored* and re-sort deterministically.

        Args:
            query: The routing query (unused by this provider; part of the
                :class:`~contextweaver.protocols.RoutingScoreProvider`
                contract so query-aware providers can use it).
            scored: ``(item_id, base_score)`` pairs.

        Returns:
            Adjusted ``(item_id, score)`` pairs sorted by ``(-score, id)``.
        """
        adjusted: list[tuple[str, float]] = []
        for item_id, score in scored:
            feedback = self._feedback.get(item_id)
            new_score = score if feedback is None else score + self._delta(feedback)
            adjusted.append((item_id, new_score))
        return _resort(adjusted)


__all__ = [
    "DEFAULT_COST_WEIGHT",
    "DEFAULT_FAILURE_PENALTY",
    "DEFAULT_LATENCY_REF_MS",
    "DEFAULT_LATENCY_WEIGHT",
    "DEFAULT_QUALITY_WEIGHT",
    "DEFAULT_SUCCESS_WEIGHT",
    "DEFAULT_TOKEN_COST_REF",
    "DeterministicScoreProvider",
    "ExecutionFeedback",
    "FeedbackAwareScoreProvider",
    "aggregate_feedback",
]
