"""Tests for contextweaver.routing.feedback (issue #318)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from contextweaver.exceptions import ConfigError
from contextweaver.routing.feedback import (
    DeterministicScoreProvider,
    ExecutionFeedback,
    FeedbackAwareScoreProvider,
    aggregate_feedback,
)
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import SelectableItem


def _item(iid: str, name: str, description: str, tags: list[str] | None = None) -> SelectableItem:
    return SelectableItem(id=iid, kind="tool", name=name, description=description, tags=tags or [])


def _catalog() -> list[SelectableItem]:
    return [
        _item("db_read", "read_db", "Read rows from the database", tags=["data"]),
        _item("db_write", "write_db", "Write rows to the database", tags=["data"]),
        _item("send_email", "send_email", "Send an email notification", tags=["comm"]),
    ]


def _build_router(**kwargs: object) -> Router:
    items = _catalog()
    graph = TreeBuilder(max_children=20).build(items)
    return Router(graph, items=items, top_k=20, **kwargs)  # type: ignore[arg-type]


# ------------------------------------------------------------------
# ExecutionFeedback serialisation
# ------------------------------------------------------------------


def test_execution_feedback_round_trip_full() -> None:
    fb = ExecutionFeedback(
        item_id="db_read",
        success=False,
        latency_ms=120.5,
        token_cost=42,
        quality_score=0.8,
        timestamp=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        metadata={"run": "abc"},
    )
    restored = ExecutionFeedback.from_dict(fb.to_dict())
    assert restored == fb


def test_execution_feedback_round_trip_minimal() -> None:
    fb = ExecutionFeedback(item_id="db_read")
    restored = ExecutionFeedback.from_dict(fb.to_dict())
    assert restored == fb
    assert restored.success is True
    assert restored.timestamp is None


# ------------------------------------------------------------------
# aggregate_feedback
# ------------------------------------------------------------------


def test_aggregate_feedback_means_and_majority() -> None:
    entries = [
        ExecutionFeedback(
            "db_read", success=True, latency_ms=100, token_cost=10, quality_score=1.0
        ),
        ExecutionFeedback(
            "db_read", success=True, latency_ms=300, token_cost=30, quality_score=0.0
        ),
        ExecutionFeedback(
            "db_read", success=False, latency_ms=200, token_cost=20, quality_score=0.5
        ),
    ]
    agg = aggregate_feedback(entries)
    assert set(agg) == {"db_read"}
    record = agg["db_read"]
    # success_rate = 2/3 >= 0.5 -> success True
    assert record.success is True
    assert record.latency_ms == pytest.approx(200.0)
    assert record.token_cost == 20
    assert record.quality_score == pytest.approx(0.5)
    assert record.metadata["sample_count"] == 3
    assert record.metadata["success_rate"] == pytest.approx(2 / 3)


def test_aggregate_feedback_majority_failure() -> None:
    entries = [
        ExecutionFeedback("x", success=False),
        ExecutionFeedback("x", success=False),
        ExecutionFeedback("x", success=True),
    ]
    assert aggregate_feedback(entries)["x"].success is False


def test_aggregate_feedback_independent_of_order() -> None:
    entries = [
        ExecutionFeedback("a", success=True, latency_ms=10),
        ExecutionFeedback("b", success=False, latency_ms=20),
        ExecutionFeedback("a", success=True, latency_ms=30),
    ]
    assert aggregate_feedback(entries) == aggregate_feedback(list(reversed(entries)))


# ------------------------------------------------------------------
# DeterministicScoreProvider
# ------------------------------------------------------------------


def test_deterministic_provider_is_identity_resort() -> None:
    provider = DeterministicScoreProvider()
    scored = [("b", 0.5), ("a", 0.5), ("c", 0.9)]
    out = provider.adjust("q", scored)
    # Scores unchanged; ties broken by ascending id.
    assert out == [("c", 0.9), ("a", 0.5), ("b", 0.5)]


# ------------------------------------------------------------------
# FeedbackAwareScoreProvider
# ------------------------------------------------------------------


def test_feedback_success_boosts_failure_penalises() -> None:
    provider = FeedbackAwareScoreProvider(
        {
            "good": ExecutionFeedback("good", success=True),
            "bad": ExecutionFeedback("bad", success=False),
        }
    )
    out = dict(provider.adjust("q", [("good", 0.5), ("bad", 0.5), ("none", 0.5)]))
    assert out["good"] == pytest.approx(0.6)  # +success_weight 0.1
    assert out["bad"] == pytest.approx(0.3)  # -failure_penalty 0.2
    assert out["none"] == pytest.approx(0.5)  # no feedback -> unchanged


def test_feedback_quality_adjusts_within_bounds() -> None:
    provider = FeedbackAwareScoreProvider(
        {
            "hi": ExecutionFeedback("hi", success=True, quality_score=1.0),
            "lo": ExecutionFeedback("lo", success=True, quality_score=0.0),
        },
        success_weight=0.0,  # isolate the quality term
        quality_weight=0.1,
    )
    out = dict(provider.adjust("q", [("hi", 0.5), ("lo", 0.5)]))
    assert out["hi"] == pytest.approx(0.6)  # +0.1 * (2*1 - 1)
    assert out["lo"] == pytest.approx(0.4)  # +0.1 * (2*0 - 1)


def test_feedback_reorders_ranking() -> None:
    # Equal base scores; feedback should push the successful item above the failed one.
    provider = FeedbackAwareScoreProvider(
        {
            "a": ExecutionFeedback("a", success=False),
            "b": ExecutionFeedback("b", success=True),
        }
    )
    out = provider.adjust("q", [("a", 0.5), ("b", 0.5)])
    assert [iid for iid, _ in out] == ["b", "a"]


def test_feedback_deterministic_tie_break_by_id() -> None:
    # Identical feedback on tied scores must break by ascending id, not input order.
    provider = FeedbackAwareScoreProvider(
        {
            "b": ExecutionFeedback("b", success=True),
            "a": ExecutionFeedback("a", success=True),
        }
    )
    out = provider.adjust("q", [("b", 0.5), ("a", 0.5)])
    assert [iid for iid, _ in out] == ["a", "b"]


def test_feedback_latency_and_cost_penalties_opt_in() -> None:
    fb = {"x": ExecutionFeedback("x", success=True, latency_ms=1000, token_cost=1000)}
    # Default weights leave latency/cost off.
    default = dict(FeedbackAwareScoreProvider(fb).adjust("q", [("x", 0.5)]))
    assert default["x"] == pytest.approx(0.6)
    # Enabling the penalties subtracts a full reference unit each.
    penalised = dict(
        FeedbackAwareScoreProvider(fb, latency_weight=0.05, cost_weight=0.05).adjust(
            "q", [("x", 0.5)]
        )
    )
    assert penalised["x"] == pytest.approx(0.6 - 0.05 - 0.05)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"success_weight": -0.1},
        {"failure_penalty": -0.1},
        {"quality_weight": -0.1},
        {"latency_weight": -0.1},
        {"cost_weight": -0.1},
        {"latency_ref_ms": 0.0},
        {"token_cost_ref": 0.0},
    ],
)
def test_feedback_invalid_config_raises(kwargs: dict[str, float]) -> None:
    with pytest.raises(ConfigError):
        FeedbackAwareScoreProvider({}, **kwargs)


def test_feedback_provider_accepts_history_sequence() -> None:
    # A flat history list is aggregated internally.
    history = [
        ExecutionFeedback("a", success=True),
        ExecutionFeedback("a", success=True),
        ExecutionFeedback("a", success=False),
    ]
    provider = FeedbackAwareScoreProvider(history)
    out = dict(provider.adjust("q", [("a", 0.5)]))
    assert out["a"] == pytest.approx(0.6)  # success_rate 2/3 -> success True


# ------------------------------------------------------------------
# Router integration
# ------------------------------------------------------------------


def test_router_default_unchanged_without_provider() -> None:
    baseline = _build_router().route("read rows from the database")
    explicit_none = _build_router(score_provider=None).route("read rows from the database")
    assert explicit_none.candidate_ids == baseline.candidate_ids
    assert explicit_none.scores == pytest.approx(baseline.scores)


def test_router_deterministic_provider_matches_default() -> None:
    baseline = _build_router().route("send an email")
    with_det = _build_router(score_provider=DeterministicScoreProvider()).route("send an email")
    assert with_det.candidate_ids == baseline.candidate_ids
    assert with_det.trace.extra.get("score_provider") == "DeterministicScoreProvider"


def test_execution_feedback_from_dict_coerces_naive_timestamp_to_utc() -> None:
    # A naive ISO string (no offset) must restore as tz-aware UTC.
    restored = ExecutionFeedback.from_dict({"item_id": "x", "timestamp": "2026-01-02T03:04:05"})
    assert restored.timestamp is not None
    assert restored.timestamp.tzinfo is timezone.utc


def test_feedback_latency_ratio_is_clamped() -> None:
    # latency far above the reference must not penalise beyond the weight.
    provider = FeedbackAwareScoreProvider(
        {"x": ExecutionFeedback("x", success=True, latency_ms=10_000)},
        success_weight=0.0,
        latency_weight=0.1,
        latency_ref_ms=1000.0,  # ratio would be 10.0 unclamped
    )
    out = dict(provider.adjust("q", [("x", 0.5)]))
    assert out["x"] == pytest.approx(0.5 - 0.1)  # clamped to exactly one weight


def test_feedback_cost_ratio_is_clamped() -> None:
    provider = FeedbackAwareScoreProvider(
        {"x": ExecutionFeedback("x", success=True, token_cost=50_000)},
        success_weight=0.0,
        cost_weight=0.1,
        token_cost_ref=1000,  # ratio would be 50.0 unclamped
    )
    out = dict(provider.adjust("q", [("x", 0.5)]))
    assert out["x"] == pytest.approx(0.5 - 0.1)


class _BadProvider:
    """A provider that violates the id-preservation contract."""

    def __init__(self, output: list[tuple[str, float]]) -> None:
        self._output = output

    def adjust(self, query: str, scored: list[tuple[str, float]]) -> list[tuple[str, float]]:
        return self._output


class _UnsortedProvider:
    """A provider that returns the right ids+scores but in scrambled order."""

    def adjust(self, query: str, scored: list[tuple[str, float]]) -> list[tuple[str, float]]:
        return list(reversed(scored))  # deliberately not re-sorted


def test_router_rejects_provider_that_drops_ids() -> None:
    from contextweaver.exceptions import RouteError

    bad = _BadProvider([("db_read", 0.9)])  # drops the other candidates
    with pytest.raises(RouteError):
        _build_router(score_provider=bad).route("database rows data")


def test_router_rejects_provider_that_duplicates_ids() -> None:
    from contextweaver.exceptions import RouteError

    # Capture the real candidate ids, then return a duplicate-laden output.
    real = _build_router().route("database rows data").candidate_ids
    dup = _BadProvider([(real[0], 0.9)] * len(real))
    with pytest.raises(RouteError):
        _build_router(score_provider=dup).route("database rows data")


def test_router_reimposes_canonical_order_when_provider_skips_resort() -> None:
    # The provider returns the same scores in reversed order; the router must
    # re-impose the canonical (-score, id) ordering, matching the no-provider
    # baseline exactly.
    baseline = _build_router().route("database rows data")
    routed = _build_router(score_provider=_UnsortedProvider()).route("database rows data")
    assert routed.candidate_ids == baseline.candidate_ids
    assert routed.scores == pytest.approx(baseline.scores)


def test_router_feedback_provider_promotes_successful_item() -> None:
    # Heavily penalise db_read so it sinks below db_write for a "data" query.
    provider = FeedbackAwareScoreProvider(
        {"db_read": ExecutionFeedback("db_read", success=False)},
        failure_penalty=10.0,
    )
    routed = _build_router(score_provider=provider).route("database rows data")
    assert routed.candidate_ids[-1] == "db_read"
    assert routed.trace.extra.get("score_provider") == "FeedbackAwareScoreProvider"
