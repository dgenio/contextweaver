"""Tests for scripts/render_trend.py — the benchmark trend page (#554).

Covers snapshot extraction (latency excluded), deterministic rendering, the
``--check`` drift gate, and version ordering across releases.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from render_trend import (  # noqa: E402
    extract_snapshot,
    load_history,
    render,
    write_snapshot,
)

_LATEST = {
    "routing": [
        {
            "catalog_size": 50,
            "recall_at_k": 0.5649,
            "mrr": 0.4978,
            "precision_at_k": 0.1191,
            "latency_ms_p99": 0.759,
        },
        {
            "catalog_size": 1000,
            "recall_at_k": 0.1475,
            "mrr": 0.1456,
            "precision_at_k": 0.031,
            "latency_ms_p99": 41.7,
        },
    ],
    "context": [
        {
            "scenario": "a",
            "items_dropped": 7,
            "dedup_removed": 4,
            "naive_delta": {"pct_reduction": 60.0},
        },
        {
            "scenario": "b",
            "items_dropped": 0,
            "dedup_removed": 0,
            "naive_delta": {"pct_reduction": 80.0},
        },
    ],
}


def test_snapshot_excludes_latency_and_averages_reduction() -> None:
    snap = extract_snapshot("1.0.0", _LATEST)
    assert snap["schema_version"] == 1
    assert snap["release"] == "1.0.0"
    metrics = snap["metrics"]
    # Latency must never leak into the snapshot.
    assert "latency" not in json.dumps(metrics)
    assert metrics["routing_recall_at_k"]["50"] == 0.5649
    assert metrics["mean_token_reduction_pct"] == 70.0  # mean(60, 80)
    assert metrics["total_items_dropped"] == 7
    assert metrics["total_dedup_removed"] == 4


def test_snapshot_roundtrip_is_byte_stable(tmp_path: Path) -> None:
    snap = extract_snapshot("1.0.0", _LATEST)
    p1 = write_snapshot(snap, tmp_path)
    first = p1.read_text(encoding="utf-8")
    second_snap = extract_snapshot("1.0.0", _LATEST)
    write_snapshot(second_snap, tmp_path)
    assert p1.read_text(encoding="utf-8") == first  # deterministic, sorted keys


def test_render_is_deterministic(tmp_path: Path) -> None:
    write_snapshot(extract_snapshot("0.16.0", _LATEST), tmp_path)
    snapshots = load_history(tmp_path)
    assert render(snapshots) == render(snapshots)
    assert "0.16.0" in render(snapshots)


def test_releases_ordered_oldest_first(tmp_path: Path) -> None:
    write_snapshot(extract_snapshot("0.16.0", _LATEST), tmp_path)
    write_snapshot(extract_snapshot("0.9.0", _LATEST), tmp_path)
    write_snapshot(extract_snapshot("0.10.0", _LATEST), tmp_path)
    releases = [s["release"] for s in load_history(tmp_path)]
    # Numeric ordering: 0.9.0 < 0.10.0 < 0.16.0 (not lexicographic).
    assert releases == ["0.9.0", "0.10.0", "0.16.0"]


def test_empty_history_renders_placeholder() -> None:
    out = render([])
    assert "No release snapshots recorded yet" in out


def test_committed_trend_is_in_sync() -> None:
    """The committed benchmarks/trend.md must match a fresh render of history."""
    root = Path(__file__).parent.parent
    snapshots = load_history(root / "benchmarks" / "results" / "history")
    committed = (root / "benchmarks" / "trend.md").read_text(encoding="utf-8")
    assert render(snapshots) == committed
