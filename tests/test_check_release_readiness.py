"""Tests for the release-readiness guard."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import check_release_readiness  # noqa: E402


def _snapshot(release: str = "1.2.3") -> dict[str, object]:
    return {
        "schema_version": check_release_readiness.SNAPSHOT_SCHEMA_VERSION,
        "release": release,
        "measurement": {
            "token_reduction_estimator": "heuristic/chardiv4",
            "token_reduction_unit": "estimated_tokens",
            "comparable_with_schema_v1_token_reduction": False,
        },
        "metrics": {
            "routing_recall_at_k": {"50": 0.5},
            "routing_mrr": {"50": 0.4},
            "routing_precision_at_k": {"50": 0.1},
            "mean_token_reduction_pct": 10.0,
            "total_items_dropped": 1,
            "total_dedup_removed": 0,
        },
    }


def _write_history(history_dir: Path, snapshot: dict[str, object]) -> None:
    history_dir.mkdir(parents=True)
    release = str(snapshot["release"])
    (history_dir / f"{release}.json").write_text(
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_current_trend(history_dir: Path, trend_path: Path) -> None:
    trend_path.write_text(
        check_release_readiness.render(check_release_readiness.load_history(history_dir)),
        encoding="utf-8",
    )


def test_missing_current_version_snapshot_is_blocking(tmp_path: Path) -> None:
    history = tmp_path / "history"
    trend = tmp_path / "trend.md"
    _write_history(history, _snapshot("1.2.2"))
    _write_current_trend(history, trend)
    problems = check_release_readiness.find_release_readiness_problems("1.2.3", history, trend)
    assert any("missing release benchmark snapshot" in problem for problem in problems)


def test_snapshot_release_field_must_match_filename_version(tmp_path: Path) -> None:
    history = tmp_path / "history"
    trend = tmp_path / "trend.md"
    history.mkdir(parents=True)
    (history / "1.2.3.json").write_text(
        json.dumps(_snapshot("1.2.2"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_current_trend(history, trend)
    problems = check_release_readiness.find_release_readiness_problems("1.2.3", history, trend)
    assert any("declares '1.2.2', expected '1.2.3'" in problem for problem in problems)


def test_legacy_snapshot_schema_is_blocking_for_current_release(tmp_path: Path) -> None:
    history = tmp_path / "history"
    trend = tmp_path / "trend.md"
    snapshot = _snapshot()
    snapshot["schema_version"] = 1
    snapshot.pop("measurement")
    _write_history(history, snapshot)
    _write_current_trend(history, trend)
    problems = check_release_readiness.find_release_readiness_problems("1.2.3", history, trend)
    assert any("expected current schema" in problem for problem in problems)
    assert any("no measurement metadata" in problem for problem in problems)


def test_stale_trend_is_blocking(tmp_path: Path) -> None:
    history = tmp_path / "history"
    trend = tmp_path / "trend.md"
    _write_history(history, _snapshot())
    trend.write_text("stale\n", encoding="utf-8")
    problems = check_release_readiness.find_release_readiness_problems("1.2.3", history, trend)
    assert any("is stale" in problem for problem in problems)


def test_complete_release_artifacts_are_ready(tmp_path: Path) -> None:
    history = tmp_path / "history"
    trend = tmp_path / "trend.md"
    _write_history(history, _snapshot())
    _write_current_trend(history, trend)
    assert check_release_readiness.find_release_readiness_problems("1.2.3", history, trend) == []


def test_real_repository_release_artifacts_are_ready() -> None:
    assert check_release_readiness.main([]) == 0
