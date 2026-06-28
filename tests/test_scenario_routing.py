"""Tests for benchmarks/scenario_routing.py — naive vs ChoiceCard (#418)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))

from scenario_routing import (  # noqa: E402
    DEFAULT_DATASET,
    render_report,
    run_all,
    run_scenario,
)


def test_scenario_rows_are_deterministic() -> None:
    a = run_all()
    b = run_all()
    assert [r.__dict__ for r in a] == [r.__dict__ for r in b]


def test_choicecards_reduce_tokens_vs_naive() -> None:
    for row in run_all():
        assert row.card_tokens < row.naive_tokens
        assert row.token_reduction_pct > 0.0
        assert row.cards_shown <= 5  # bounded by TOP_K


def test_rank_consistent_with_top_k_flag() -> None:
    for row in run_all():
        # rank > 0 iff the expected tool is in the shortlist.
        assert (row.correct_rank > 0) == row.correct_in_top_k


def test_dataset_covers_multiple_namespaces() -> None:
    import json

    scenarios = json.loads(DEFAULT_DATASET.read_text(encoding="utf-8"))
    namespaces = {expected.split(".", 1)[0] for row in scenarios for expected in row["expected"]}
    assert len(namespaces) >= 3


def test_large_result_is_firewalled_and_viewable() -> None:
    row = {row.name: row for row in run_all()}["revenue_report"]
    assert row.raw_result_chars == 8000
    assert 0 < row.injected_result_chars < row.raw_result_chars
    assert row.raw_result_exposed is False
    assert row.artifact_created is True
    assert row.tool_view_recovered is True


def test_ambiguous_route_asks_and_hides_destructive_tools() -> None:
    row = {row.name: row for row in run_all()}["ambiguous_customer_account"]
    assert row.ambiguous is True
    assert row.clarifying_question is True
    assert row.destructive_cards_shown == 0
    assert row.route_work_units > 0


def test_render_is_deterministic_and_matches_commit() -> None:
    rows = run_all()
    assert render_report(rows) == render_report(rows)
    committed = (Path(__file__).parent.parent / "benchmarks" / "scenario_routing.md").read_text(
        encoding="utf-8"
    )
    assert render_report(rows) == committed


def test_single_scenario_shape() -> None:
    import json

    first = json.loads(DEFAULT_DATASET.read_text(encoding="utf-8"))[0]
    row = run_scenario(first)
    assert row.name == first["name"]
    assert row.catalog_size == first["catalog_size"]
