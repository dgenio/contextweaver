"""Tests for scripts/render_trend.py — release benchmark history."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from render_trend import (  # noqa: E402
    SNAPSHOT_SCHEMA_VERSION,
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
            "naive_delta": {
                "pct_reduction": 60.0,
                "token_estimator": "heuristic/chardiv4",
            },
        },
        {
            "scenario": "b",
            "items_dropped": 0,
            "dedup_removed": 0,
            "naive_delta": {
                "pct_reduction": 80.0,
                "token_estimator": "heuristic/chardiv4",
            },
        },
    ],
}


def test_snapshot_excludes_latency_and_records_measurement() -> None:
    snap = extract_snapshot("1.0.0", _LATEST)
    assert snap["schema_version"] == SNAPSHOT_SCHEMA_VERSION == 2
    assert snap["release"] == "1.0.0"
    assert snap["measurement"] == {
        "token_reduction_estimator": "heuristic/chardiv4",
        "token_reduction_unit": "estimated_tokens",
        "comparable_with_schema_v1_token_reduction": False,
    }
    metrics = snap["metrics"]
    assert "latency" not in json.dumps(metrics)
    assert metrics["routing_recall_at_k"]["50"] == 0.5649
    assert metrics["mean_token_reduction_pct"] == 70.0
    assert metrics["total_items_dropped"] == 7
    assert metrics["total_dedup_removed"] == 4


def test_snapshot_fails_when_reduction_method_is_missing() -> None:
    latest = json.loads(json.dumps(_LATEST))
    del latest["context"][0]["naive_delta"]["token_estimator"]
    with pytest.raises(ValueError, match="missing explicit token_estimator"):
        extract_snapshot("1.0.0", latest)


def test_snapshot_fails_when_reduction_methods_disagree() -> None:
    latest = json.loads(json.dumps(_LATEST))
    latest["context"][1]["naive_delta"]["token_estimator"] = "cl100k_base"
    with pytest.raises(ValueError, match="inconsistent estimators"):
        extract_snapshot("1.0.0", latest)


def test_snapshot_roundtrip_is_byte_stable(tmp_path: Path) -> None:
    snap = extract_snapshot("1.0.0", _LATEST)
    path = write_snapshot(snap, tmp_path)
    first = path.read_text(encoding="utf-8")
    write_snapshot(extract_snapshot("1.0.0", _LATEST), tmp_path)
    assert path.read_text(encoding="utf-8") == first


def test_render_marks_schema_v1_token_history_as_legacy(tmp_path: Path) -> None:
    legacy = {
        "schema_version": 1,
        "release": "0.18.0",
        "metrics": {
            "routing_recall_at_k": {},
            "routing_mrr": {},
            "routing_precision_at_k": {},
            "mean_token_reduction_pct": 65.01,
            "total_items_dropped": 0,
            "total_dedup_removed": 0,
        },
    }
    write_snapshot(legacy, tmp_path)
    write_snapshot(extract_snapshot("0.18.1", _LATEST), tmp_path)
    output = render(load_history(tmp_path))
    assert "legacy/unverified†" in output
    assert "heuristic/chardiv4" in output
    assert "not treated as directly" in output


def test_render_is_deterministic(tmp_path: Path) -> None:
    write_snapshot(extract_snapshot("0.18.1", _LATEST), tmp_path)
    snapshots = load_history(tmp_path)
    assert render(snapshots) == render(snapshots)


def test_releases_ordered_oldest_first(tmp_path: Path) -> None:
    write_snapshot(extract_snapshot("0.16.0", _LATEST), tmp_path)
    write_snapshot(extract_snapshot("0.9.0", _LATEST), tmp_path)
    write_snapshot(extract_snapshot("0.10.0", _LATEST), tmp_path)
    releases = [snapshot["release"] for snapshot in load_history(tmp_path)]
    assert releases == ["0.9.0", "0.10.0", "0.16.0"]


def test_empty_history_renders_placeholder() -> None:
    assert "No release snapshots recorded yet" in render([])


def test_publish_workflow_requires_immutable_tag_and_current_evidence() -> None:
    root = Path(__file__).parent.parent
    workflow = (root / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow
    assert "python scripts/check_release_readiness.py" in workflow
    assert "Dispatch this workflow on the immutable vX.Y.Z tag" in workflow
    assert "Release ref '$tag' does not match package version" in workflow


def test_committed_trend_is_in_sync() -> None:
    root = Path(__file__).parent.parent
    snapshots = load_history(root / "benchmarks" / "results" / "history")
    committed = (root / "benchmarks" / "trend.md").read_text(encoding="utf-8")
    assert render(snapshots) == committed
