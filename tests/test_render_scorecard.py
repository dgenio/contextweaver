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


# ---------------------------------------------------------------------------
# Matrix / per-namespace / naïve-baseline sections (#208 / #209 / #215)
# ---------------------------------------------------------------------------


def _payload_with_matrix() -> dict[str, object]:
    """A sample payload that exercises every new section."""
    payload = json.loads(json.dumps(_SAMPLE_PAYLOAD))
    payload["matrix"] = [
        {
            "backend": "tfidf",
            "catalog_size": 100,
            "queries_evaluated": 200,
            "precision_at_k": 0.078,
            "recall_at_k": 0.38,
            "mrr": 0.32,
            "latency_ms_p50": 0.6,
            "latency_ms_p95": 0.8,
            "latency_ms_p99": 1.0,
            "status": "",
        },
        {
            "backend": "bm25",
            "catalog_size": 100,
            "queries_evaluated": 200,
            "precision_at_k": 0.078,
            "recall_at_k": 0.38,
            "mrr": 0.32,
            # 2.0 > 1.0 * 1.30 = 1.3 → ⚠️
            "latency_ms_p50": 1.5,
            "latency_ms_p95": 1.8,
            "latency_ms_p99": 2.0,
            "status": "",
        },
        {
            "backend": "fuzzy",
            "catalog_size": 100,
            "queries_evaluated": 0,
            "precision_at_k": 0.0,
            "recall_at_k": 0.0,
            "mrr": 0.0,
            "latency_ms_p50": 0.0,
            "latency_ms_p95": 0.0,
            "latency_ms_p99": 0.0,
            "status": "skipped: rapidfuzz not installed",
        },
    ]
    payload["per_namespace"] = {
        "tfidf": {"admin": 0.40, "billing": 0.25},
        "bm25": {"admin": 0.45, "billing": 0.20},
    }
    payload["context"][0]["naive_delta"] = {
        "naive_tokens": 15954.0,
        "cw_tokens": 6651.0,
        "pct_reduction": 58.31,
        "coverage_pct": 96.88,
    }
    payload["context"][1]["naive_delta"] = {
        "naive_tokens": 418.0,
        "cw_tokens": 496.0,
        "pct_reduction": 0.0,
        "coverage_pct": 100.0,
    }
    return payload


def test_render_includes_matrix_section_when_present() -> None:
    md = render_scorecard.render(_payload_with_matrix())
    assert "## Per-backend × per-size matrix (#208)" in md
    # tfidf p99=1.0 is the fastest → ✅; bm25 p99=2.0 exceeds 1.30× → ⚠️.
    matrix_section = md.split("## Per-backend × per-size matrix", 1)[1]
    bm25_line = next(ln for ln in matrix_section.splitlines() if "| bm25 | 100 |" in ln)
    tfidf_line = next(ln for ln in matrix_section.splitlines() if "| tfidf | 100 |" in ln)
    assert "⚠️" in bm25_line
    assert "✅" in tfidf_line


def test_render_renders_skipped_matrix_row_as_status_note() -> None:
    md = render_scorecard.render(_payload_with_matrix())
    # The fuzzy row carries a non-empty status; the renderer should embed
    # that string italicised rather than reporting fake zero metrics.
    matrix_section = md.split("## Per-backend × per-size matrix", 1)[1]
    fuzzy_line = next(ln for ln in matrix_section.splitlines() if "| fuzzy | 100 |" in ln)
    assert "skipped: rapidfuzz not installed" in fuzzy_line
    # Skipped rows must NOT carry a marker (they didn't run).
    assert "⚠️" not in fuzzy_line
    assert "✅" not in fuzzy_line


def test_render_includes_per_namespace_section() -> None:
    md = render_scorecard.render(_payload_with_matrix())
    assert "## Per-namespace recall (#209)" in md
    ns_section = md.split("## Per-namespace recall (#209)", 1)[1]
    # Sorted by namespace name across backends.
    admin_idx = ns_section.find("| admin |")
    billing_idx = ns_section.find("| billing |")
    assert admin_idx != -1 and billing_idx != -1
    assert admin_idx < billing_idx


def test_render_includes_naive_section_and_average() -> None:
    md = render_scorecard.render(_payload_with_matrix())
    assert "## vs naïve concat (#215)" in md
    # Average across the two scenarios is (58.31 + 0.0) / 2 = 29.16.
    assert "29.16" in md


def test_render_omits_naive_section_when_no_delta() -> None:
    """Payload without any naive_delta entry doesn't render the naïve section."""
    payload = json.loads(json.dumps(_SAMPLE_PAYLOAD))
    md = render_scorecard.render(payload)
    assert "vs naïve concat" not in md


def test_matrix_validation_rejects_missing_field() -> None:
    """Matrix rows missing required keys raise ValueError naming the field."""
    payload = _payload_with_matrix()
    del payload["matrix"][0]["mrr"]  # type: ignore[index]
    with pytest.raises(ValueError, match=r"mrr"):
        render_scorecard.render(payload)
