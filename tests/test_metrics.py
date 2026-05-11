"""Tests for contextweaver.metrics — MetricsCollector + MetricsHook."""

from __future__ import annotations

from contextweaver.envelope import BuildStats, ContextPack
from contextweaver.metrics import MetricsCollector, MetricsHook
from contextweaver.protocols import EventHook
from contextweaver.routing.router import RouteResult
from contextweaver.types import ContextItem, ItemKind, Phase


def _pack(
    *,
    tokens: int = 100,
    dropped: int = 0,
    dedup: int = 0,
    drop_reasons: dict[str, int] | None = None,
) -> ContextPack:
    """Build a ContextPack with the given diagnostic stats."""
    stats = BuildStats(
        tokens_per_section={"body": tokens},
        total_candidates=10,
        included_count=10 - dropped,
        dropped_count=dropped,
        dropped_reasons=drop_reasons or {},
        dedup_removed=dedup,
        header_footer_tokens=20,
    )
    return ContextPack(prompt="", stats=stats, phase=Phase.answer)


def _item(iid: str = "u1") -> ContextItem:
    return ContextItem(id=iid, kind=ItemKind.user_turn, text="hello")


# ---------------------------------------------------------------------------
# MetricsCollector — direct accumulation
# ---------------------------------------------------------------------------


def test_collector_starts_empty() -> None:
    c = MetricsCollector()
    s = c.summary()
    assert s["total_builds"] == 0
    assert s["total_routes"] == 0
    assert s["total_prompt_tokens"] == 0
    assert s["drop_reasons"] == {}


def test_record_build_accumulates_tokens_and_drops() -> None:
    c = MetricsCollector()
    c.record_build(_pack(tokens=100, dropped=2, dedup=1, drop_reasons={"budget": 2}))
    c.record_build(_pack(tokens=200, dropped=3, dedup=0, drop_reasons={"budget": 3}))
    s = c.summary()
    assert s["total_builds"] == 2
    # 100+20 + 200+20 (header_footer adds 20 each)
    assert s["total_prompt_tokens"] == 340
    assert s["total_dropped"] == 5
    assert s["total_dedup_removed"] == 1
    assert s["drop_reasons"] == {"budget": 5}
    assert s["avg_prompt_tokens"] == 170.0


def test_record_build_merges_drop_reasons() -> None:
    c = MetricsCollector()
    c.record_build(_pack(drop_reasons={"budget": 2}))
    c.record_build(_pack(drop_reasons={"sensitivity": 1}))
    s = c.summary()
    assert s["drop_reasons"] == {"budget": 2, "sensitivity": 1}


def test_record_route_captures_top_score_and_gap() -> None:
    c = MetricsCollector()
    r = RouteResult(
        candidate_items=[],
        candidate_ids=["a", "b", "c"],
        scores=[0.9, 0.5, 0.3],
        paths=[],
    )
    c.record_route(r)
    s = c.summary()
    assert s["total_routes"] == 1
    assert s["avg_candidates_per_route"] == 3.0
    assert s["avg_top_score"] == 0.9
    assert s["avg_confidence_gap"] == 0.4


def test_record_route_empty_result_safe() -> None:
    c = MetricsCollector()
    c.record_route(RouteResult())
    s = c.summary()
    assert s["total_routes"] == 1
    assert s["avg_candidates_per_route"] == 0.0
    assert s["avg_top_score"] == 0.0
    assert s["avg_confidence_gap"] == 0.0


def test_record_route_single_candidate_zero_gap() -> None:
    c = MetricsCollector()
    c.record_route(RouteResult(candidate_ids=["only"], scores=[0.7]))
    assert c.summary()["avg_confidence_gap"] == 0.0


def test_record_route_uses_running_sums_and_maxima() -> None:
    """Per-route stats track running sums + maxima, not unbounded lists.

    Regression for PR #188 review — the earlier implementation stored
    every route's candidate count, top score, and confidence gap in
    `list[...]` fields, leaking memory in long-running processes. The
    collector now keeps O(1) state per route; this test confirms that
    averages still match and that the new max_* fields surface correctly.
    """
    c = MetricsCollector()
    c.record_route(RouteResult(candidate_ids=["a", "b", "c"], scores=[0.9, 0.5, 0.3]))
    c.record_route(RouteResult(candidate_ids=["a", "b"], scores=[0.7, 0.65]))
    c.record_route(
        RouteResult(
            candidate_ids=["a", "b", "c", "d", "e"],
            scores=[0.95, 0.4, 0.3, 0.2, 0.1],
        )
    )
    s = c.summary()
    # Running maxima (new fields)
    assert s["max_candidates_per_route"] == 5
    assert s["max_top_score"] == 0.95
    # Confidence gaps per call: 0.4, 0.05, 0.55 → max = 0.55
    assert s["max_confidence_gap"] == 0.55
    # Averages still computed from running sums
    assert s["avg_candidates_per_route"] == round((3 + 2 + 5) / 3, 4)
    assert s["total_routes"] == 3


def test_firewall_and_excluded_counters() -> None:
    c = MetricsCollector()
    c.record_firewall()
    c.record_firewall()
    c.record_items_excluded(3)
    c.record_budget_exceeded()
    s = c.summary()
    assert s["firewall_interceptions"] == 2
    assert s["items_excluded"] == 3
    assert s["budget_exceeded"] == 1


def test_summary_keys_are_sorted_alphabetically() -> None:
    c = MetricsCollector()
    keys = list(c.summary().keys())
    assert keys == sorted(keys)


def test_reset_zeroes_all_counters() -> None:
    c = MetricsCollector()
    c.record_build(_pack(tokens=50, dropped=1, drop_reasons={"budget": 1}))
    c.record_route(RouteResult(candidate_ids=["a"], scores=[0.5]))
    c.record_firewall()
    c.reset()
    s = c.summary()
    assert s["total_builds"] == 0
    assert s["total_routes"] == 0
    assert s["firewall_interceptions"] == 0
    assert s["drop_reasons"] == {}


# ---------------------------------------------------------------------------
# MetricsHook — EventHook integration
# ---------------------------------------------------------------------------


def test_metrics_hook_satisfies_event_hook_protocol() -> None:
    hook = MetricsHook()
    assert isinstance(hook, EventHook)


def test_metrics_hook_owns_a_collector_by_default() -> None:
    hook = MetricsHook()
    assert isinstance(hook.collector, MetricsCollector)


def test_metrics_hook_shares_external_collector() -> None:
    shared = MetricsCollector()
    hook = MetricsHook(collector=shared)
    assert hook.collector is shared


def test_metrics_hook_on_context_built_records_to_collector() -> None:
    hook = MetricsHook()
    hook.on_context_built(_pack(tokens=80))
    s = hook.collector.summary()
    assert s["total_builds"] == 1
    assert s["total_prompt_tokens"] == 100  # 80 + 20 header_footer


def test_metrics_hook_firewall_callback() -> None:
    hook = MetricsHook()
    hook.on_firewall_triggered(_item(), reason="size_limit")
    assert hook.collector.summary()["firewall_interceptions"] == 1


def test_metrics_hook_items_excluded_counts_length() -> None:
    hook = MetricsHook()
    hook.on_items_excluded([_item("a"), _item("b"), _item("c")], reason="budget")
    assert hook.collector.summary()["items_excluded"] == 3


def test_metrics_hook_budget_exceeded_counter() -> None:
    hook = MetricsHook()
    hook.on_budget_exceeded(requested=2000, budget=1000)
    assert hook.collector.summary()["budget_exceeded"] == 1


def test_metrics_hook_route_completed_is_intentionally_no_op() -> None:
    """Route metrics arrive via ContextManager.metrics, not the hook."""
    hook = MetricsHook()
    hook.on_route_completed(["a", "b"])
    # No route counted from the hook path.
    assert hook.collector.summary()["total_routes"] == 0


# ---------------------------------------------------------------------------
# Cross-build aggregation
# ---------------------------------------------------------------------------


def test_collector_aggregates_across_many_builds() -> None:
    c = MetricsCollector()
    for i in range(10):
        c.record_build(_pack(tokens=100 + i, dropped=i % 3))
        c.record_route(
            RouteResult(candidate_ids=[f"x{j}" for j in range(i + 1)], scores=[0.9 - 0.05 * i])
        )
    s = c.summary()
    assert s["total_builds"] == 10
    assert s["total_routes"] == 10
    assert s["avg_candidates_per_route"] > 0.0
