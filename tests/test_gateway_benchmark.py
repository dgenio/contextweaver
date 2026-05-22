"""Tests for the gateway-scenario benchmark suite (issue #270)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "benchmarks"))

import gateway_benchmark  # noqa: E402

_RENDER_PATH = _REPO_ROOT / "scripts" / "render_gateway_scorecard.py"
_render_spec = importlib.util.spec_from_file_location("render_gateway_scorecard", _RENDER_PATH)
assert _render_spec is not None and _render_spec.loader is not None
render_gateway_scorecard = importlib.util.module_from_spec(_render_spec)
_render_spec.loader.exec_module(render_gateway_scorecard)


@pytest.fixture(scope="module")
def benchmark_payload() -> dict:
    """Run the benchmark once per module to keep test runtime small."""
    return gateway_benchmark.run_all()


def test_runs_five_scenarios(benchmark_payload: dict) -> None:
    """The harness ships five scenarios — tiny/small/medium/large/rowset."""
    assert benchmark_payload["aggregate"]["n_scenarios"] == 5
    names = [s["scenario"] for s in benchmark_payload["scenarios"]]
    assert names == [
        "tiny_ack",
        "small_post",
        "medium_ticket",
        "large_log",
        "bigquery_rowset",
    ]


def test_payload_includes_required_top_level_keys(benchmark_payload: dict) -> None:
    """Every committed gateway_latest.json must carry these fields so
    render_gateway_scorecard can build the markdown deterministically."""
    for key in ("benchmark_version", "catalog_path", "scenarios", "aggregate"):
        assert key in benchmark_payload, f"top-level key {key!r} missing"
    for key in (
        "n_scenarios",
        "firewall_reduction_min_pct",
        "firewall_reduction_max_pct",
        "raw_chars_min",
        "raw_chars_max",
        "injected_summary_chars_min",
        "injected_summary_chars_max",
    ):
        assert key in benchmark_payload["aggregate"], f"aggregate.{key} missing"


def test_each_scenario_uses_the_60_tool_catalog(benchmark_payload: dict) -> None:
    """All scenarios route against the same architecture catalog (60 tools)
    — this is the load-bearing premise of the suite."""
    for row in benchmark_payload["scenarios"]:
        assert row["catalog_tools"] == 60, f"{row['scenario']} routed against wrong catalog"


def test_firewall_reduction_range_spans_zero_to_high(benchmark_payload: dict) -> None:
    """The headline range must cover the 0 % floor (small payloads) and a
    >90 % high-water mark (large rowset) — otherwise the suite is not doing
    its job of showing the firewall's operating envelope."""
    agg = benchmark_payload["aggregate"]
    assert agg["firewall_reduction_min_pct"] == 0.0, (
        f"reduction floor moved from 0.0 to {agg['firewall_reduction_min_pct']} — "
        "either a small-payload scenario was removed or the threshold changed"
    )
    assert agg["firewall_reduction_max_pct"] > 90.0


def test_small_payloads_do_not_create_artifacts(benchmark_payload: dict) -> None:
    """The firewall must not store an artifact for any payload under the
    threshold. This is the inverse of the reduction-range check above."""
    for row in benchmark_payload["scenarios"]:
        if row["raw_result_chars"] < 1000:
            assert not row["artifact_created"], (
                f"scenario {row['scenario']!r} ({row['raw_result_chars']} chars) "
                "unexpectedly created an artifact"
            )


def test_large_payloads_do_create_artifacts(benchmark_payload: dict) -> None:
    """The firewall must store an artifact for any payload over the
    threshold so tool_view can drill back in."""
    for row in benchmark_payload["scenarios"]:
        if row["raw_result_chars"] > 5000:
            assert row["artifact_created"], (
                f"scenario {row['scenario']!r} ({row['raw_result_chars']} chars) "
                "did not create an artifact — firewall bypassed?"
            )


def test_run_all_is_deterministic(benchmark_payload: dict) -> None:
    """Re-running the harness must produce the same JSON payload byte-for-byte
    (no clocks, no rng without a fixed seed). If this breaks, the
    committed gateway_latest.json will drift in CI."""
    second = gateway_benchmark.run_all()
    assert json.dumps(second, sort_keys=True) == json.dumps(benchmark_payload, sort_keys=True)


def test_renderer_produces_byte_stable_output(benchmark_payload: dict, tmp_path: Path) -> None:
    """The scorecard renderer must be deterministic for a given payload."""
    first = render_gateway_scorecard._render(benchmark_payload)
    second = render_gateway_scorecard._render(benchmark_payload)
    assert first == second
    # Smoke-test: the rendered markdown must mention the headline range.
    assert "0.0%" in first
    assert "98.8%" in first
    assert "5 gateway scenarios" in first
