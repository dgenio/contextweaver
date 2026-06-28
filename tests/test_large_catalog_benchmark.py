"""Tests for benchmarks/large_catalog.py — the 300+ tool benchmark (#369).

CI-safe: runs a reduced catalog so the suite stays fast, and asserts the
structural contract (size, namespaces, distractors, deny filtering) plus
accuracy/token determinism.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))

from large_catalog import (  # noqa: E402
    build_large_catalog,
    render_scorecard,
    run_benchmark,
)


def test_catalog_has_requested_size_and_many_namespaces() -> None:
    items = build_large_catalog(n=320, seed=42)
    assert len(items) == 320
    namespaces = {it.namespace for it in items if it.namespace}
    assert len(namespaces) >= 5  # acceptance: >= 5 upstream namespaces
    # Distractor variants exist and carry distinct ids from the base pool.
    assert any(".v" in it.id for it in items)


def test_results_are_deterministic() -> None:
    a = run_benchmark(n=160, seed=42)
    b = run_benchmark(n=160, seed=42)
    assert a.recall_at_5 == b.recall_at_5
    assert a.mrr == b.mrr
    assert a.mean_card_tokens == b.mean_card_tokens
    assert a.token_reduction_pct == b.token_reduction_pct


def test_choicecards_collapse_the_prompt() -> None:
    result = run_benchmark(n=160, seed=42)
    assert result.mean_card_tokens < result.mean_naive_tokens
    assert result.mean_card_chars < result.naive_prompt_chars
    assert result.token_reduction_pct > 50.0


def test_denied_destructive_tools_never_reach_shortlist() -> None:
    result = run_benchmark(n=160, seed=42)
    assert result.destructive_tools > 0
    assert result.destructive_in_shortlist_denied == 0


def test_namespace_filter_and_firewall_contracts() -> None:
    result = run_benchmark(n=160, seed=42)
    assert result.namespace_filtered_recall_at_5 >= result.recall_at_5
    assert result.namespace_filter_leaks == 0
    assert result.firewall_summary_chars < result.firewall_raw_chars
    assert result.firewall_artifact_created is True
    assert result.raw_result_exposed_inline is False
    assert result.tool_view_recovered is True


def test_scorecard_render_is_deterministic() -> None:
    result = run_benchmark(n=160, seed=42)
    assert render_scorecard(result) == render_scorecard(result)


def test_committed_scorecard_matches_full_run() -> None:
    """The committed 320-tool scorecard must match a fresh full run."""
    result = run_benchmark()  # defaults: 320 tools, seed 42
    committed = (
        Path(__file__).parent.parent / "benchmarks" / "large_catalog_scorecard.md"
    ).read_text(encoding="utf-8")
    assert render_scorecard(result) == committed
