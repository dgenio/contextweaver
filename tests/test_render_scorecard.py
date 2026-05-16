"""Tests for the benchmark scorecard renderer (#197).

The renderer must be deterministic — identical input JSON yields byte-identical
markdown — and must reject inputs missing the fields it relies on. CI's
``scorecard-check`` step is the production gate; these tests pin the
behaviour at unit level so the renderer isn't only validated through the
end-to-end ``make benchmark && make scorecard`` flow.

The renderer lives under ``scripts/``, not ``src/``, so we add it to
``sys.path`` the same way :mod:`tests.test_benchmark` does for
``benchmarks/benchmark.py``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import render_scorecard  # noqa: E402  (import after sys.path manipulation)

_SAMPLE_PAYLOAD: dict[str, object] = {
    "benchmark_version": "1.0",
    "k": 5,
    "seed": 42,
    "routing": [
        {
            "catalog_size": 50,
            "queries_evaluated": 50,
            "precision_at_k": 0.16,
            "recall_at_k": 0.74,
            "mrr": 0.71,
            "latency_ms_p50": 0.124,
            "latency_ms_p95": 0.272,
            "latency_ms_p99": 0.452,
        },
        {
            "catalog_size": 1000,
            "queries_evaluated": 50,
            "precision_at_k": 0.064,
            "recall_at_k": 0.31,
            "mrr": 0.32,
            "latency_ms_p50": 0.353,
            "latency_ms_p95": 0.742,
            "latency_ms_p99": 1.057,
        },
    ],
    "context": [
        {
            "scenario": "stress_conversation",
            "event_count": 147,
            "items_included": 136,
            "items_dropped": 7,
            "dedup_removed": 4,
            "prompt_tokens": 6651,
            "budget_tokens": 6000,
            "budget_utilization_pct": 110.9,
            "artifacts_created": 32,
            "avg_compaction_ratio": 3.29,
        },
        {
            "scenario": "short_conversation",
            "event_count": 18,
            "items_included": 18,
            "items_dropped": 0,
            "dedup_removed": 0,
            "prompt_tokens": 496,
            "budget_tokens": 6000,
            "budget_utilization_pct": 8.3,
            "artifacts_created": 4,
            "avg_compaction_ratio": 1.0,
        },
    ],
}


def test_render_contains_header_and_metadata() -> None:
    """Rendered markdown advertises the harness version, seed, k, and budget."""
    md = render_scorecard.render(_SAMPLE_PAYLOAD)
    assert md.startswith("# contextweaver — Benchmark Scorecard\n")
    assert "Harness version: `1.0`" in md
    assert "Seed: `42`" in md
    assert "Rank cutoff `k`: `5`" in md
    assert "6000" in md  # answer-phase budget


def test_render_routing_table_includes_all_rows_in_ascending_size() -> None:
    """Routing rows are ordered by catalog_size ascending and use 4-dp accuracy."""
    md = render_scorecard.render(_SAMPLE_PAYLOAD)
    routing_section = md.split("## Routing accuracy & latency", 1)[1].split("\n---\n", 1)[0]
    idx_50 = routing_section.find("| 50 |")
    idx_1000 = routing_section.find("| 1000 |")
    assert idx_50 != -1 and idx_1000 != -1
    assert idx_50 < idx_1000  # ascending
    # 4-dp accuracy formatting on the recall@k value.
    assert "0.7400" in routing_section
    assert "0.3100" in routing_section


def test_render_context_table_sorted_by_scenario_name() -> None:
    """Context rows are ordered alphabetically by scenario, regardless of input order."""
    md = render_scorecard.render(_SAMPLE_PAYLOAD)
    context_section = md.split("## Context pipeline scenarios", 1)[1].split("\n---\n", 1)[0]
    idx_short = context_section.find("| short_conversation |")
    idx_stress = context_section.find("| stress_conversation |")
    assert idx_short != -1 and idx_stress != -1
    assert idx_short < idx_stress  # alphabetical, not insertion order
    # Compaction ratio surfaces with two decimal places.
    assert "3.29x" in context_section
    assert "1.00x" in context_section


def test_render_is_deterministic() -> None:
    """Two consecutive renders of the same payload produce byte-identical output."""
    md_a = render_scorecard.render(_SAMPLE_PAYLOAD)
    md_b = render_scorecard.render(_SAMPLE_PAYLOAD)
    assert md_a == md_b


def test_render_rejects_missing_top_level_key() -> None:
    """Missing top-level keys raise :class:`ValueError` with the missing names."""
    payload = dict(_SAMPLE_PAYLOAD)
    payload.pop("seed")
    with pytest.raises(ValueError, match=r"seed"):
        render_scorecard.render(payload)


def test_render_rejects_missing_routing_field() -> None:
    """Missing routing-row fields raise :class:`ValueError` naming the field."""
    payload = json.loads(json.dumps(_SAMPLE_PAYLOAD))  # deep copy
    del payload["routing"][0]["mrr"]
    with pytest.raises(ValueError, match=r"mrr"):
        render_scorecard.render(payload)


def test_render_rejects_missing_context_field() -> None:
    """Missing context-row fields raise :class:`ValueError` naming the field."""
    payload = json.loads(json.dumps(_SAMPLE_PAYLOAD))
    del payload["context"][0]["avg_compaction_ratio"]
    with pytest.raises(ValueError, match=r"avg_compaction_ratio"):
        render_scorecard.render(payload)


def test_main_check_mode_passes_when_committed_file_matches(tmp_path: Path) -> None:
    """``--check`` exits 0 when the on-disk markdown matches the rendered version."""
    input_path = tmp_path / "latest.json"
    output_path = tmp_path / "scorecard.md"
    input_path.write_text(json.dumps(_SAMPLE_PAYLOAD), encoding="utf-8")
    output_path.write_text(render_scorecard.render(_SAMPLE_PAYLOAD), encoding="utf-8")
    rc = render_scorecard.main(
        ["--input", str(input_path), "--output", str(output_path), "--check"]
    )
    assert rc == 0


def test_main_check_mode_fails_on_drift(tmp_path: Path) -> None:
    """``--check`` exits non-zero when the on-disk markdown is stale."""
    input_path = tmp_path / "latest.json"
    output_path = tmp_path / "scorecard.md"
    input_path.write_text(json.dumps(_SAMPLE_PAYLOAD), encoding="utf-8")
    output_path.write_text("# stale scorecard\n", encoding="utf-8")
    rc = render_scorecard.main(
        ["--input", str(input_path), "--output", str(output_path), "--check"]
    )
    assert rc == 1


def test_main_check_mode_fails_when_output_missing(tmp_path: Path) -> None:
    """``--check`` exits non-zero when the output file does not yet exist."""
    input_path = tmp_path / "latest.json"
    output_path = tmp_path / "missing.md"
    input_path.write_text(json.dumps(_SAMPLE_PAYLOAD), encoding="utf-8")
    rc = render_scorecard.main(
        ["--input", str(input_path), "--output", str(output_path), "--check"]
    )
    assert rc == 1


def test_main_writes_output(tmp_path: Path) -> None:
    """Without ``--check``, ``main`` writes the rendered markdown to disk."""
    input_path = tmp_path / "latest.json"
    output_path = tmp_path / "scorecard.md"
    input_path.write_text(json.dumps(_SAMPLE_PAYLOAD), encoding="utf-8")
    rc = render_scorecard.main(["--input", str(input_path), "--output", str(output_path)])
    assert rc == 0
    rendered = output_path.read_text(encoding="utf-8")
    assert rendered == render_scorecard.render(_SAMPLE_PAYLOAD)


def test_main_errors_when_input_missing(tmp_path: Path) -> None:
    """``main`` exits non-zero (with a helpful message) when the input is absent."""
    rc = render_scorecard.main(
        ["--input", str(tmp_path / "no-such.json"), "--output", str(tmp_path / "out.md")]
    )
    assert rc == 1
