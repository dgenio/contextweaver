"""Tests for scripts/benchmark_gate.py — the quality-regression gate (#491).

Covers the gate contract: a quality metric regressing beyond its band fails;
within-band movement and improvements pass; latency never gates; new/removed
cells do not fire; the override downgrades a failure to a warning.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from benchmark_gate import (  # noqa: E402
    GatingConfig,
    evaluate_gate,
    load_gating_config,
    main,
)

_CONFIG = GatingConfig(
    bands={
        "recall_at_k": 1.0,
        "mrr": 1.0,
        "precision_at_k": 1.0,
        "token_savings_pct": 2.0,
        "avg_compaction_ratio": 5.0,
    }
)


def _routing(recall: float, mrr: float = 0.3, size: int = 100) -> dict[str, object]:
    return {
        "routing": [
            {
                "catalog_size": size,
                "recall_at_k": recall,
                "mrr": mrr,
                "precision_at_k": 0.08,
                "latency_ms_p99": 1.0,
            }
        ]
    }


def test_recall_regression_beyond_band_fails() -> None:
    base = _routing(0.5000)
    head = _routing(0.4800)  # -2.0pp, band is 1.0pp
    violations = evaluate_gate(base, head, _CONFIG)
    assert len(violations) == 1
    v = violations[0]
    assert v.metric == "recall_at_k"
    assert v.cell == "routing/size=100"
    assert round(v.regression or 0.0, 2) == 2.0


def test_recall_within_band_passes() -> None:
    base = _routing(0.5000)
    head = _routing(0.4950)  # -0.5pp, inside the 1.0pp band
    assert evaluate_gate(base, head, _CONFIG) == []


def test_improvement_passes() -> None:
    base = _routing(0.5000)
    head = _routing(0.6000)
    assert evaluate_gate(base, head, _CONFIG) == []


def test_latency_never_gates() -> None:
    base = {"routing": [{"catalog_size": 100, "latency_ms_p99": 1.0}]}
    head = {"routing": [{"catalog_size": 100, "latency_ms_p99": 1000.0}]}
    assert evaluate_gate(base, head, _CONFIG) == []


def test_new_cell_does_not_fire() -> None:
    base = _routing(0.50, size=100)
    head = {
        "routing": [
            {"catalog_size": 100, "recall_at_k": 0.50, "mrr": 0.3, "precision_at_k": 0.08},
            {"catalog_size": 500, "recall_at_k": 0.01, "mrr": 0.01, "precision_at_k": 0.0},
        ]
    }
    # The size=500 cell is new (absent in base) — it cannot "regress".
    assert evaluate_gate(base, head, _CONFIG) == []


def test_skipped_matrix_cell_ignored() -> None:
    base = {
        "routing_matrix": [
            {"backend": "fuzzy", "catalog_size": 100, "status": "skipped: missing rapidfuzz"}
        ]
    }
    head = {
        "routing_matrix": [
            {"backend": "fuzzy", "catalog_size": 100, "status": "skipped: missing rapidfuzz"}
        ]
    }
    assert evaluate_gate(base, head, _CONFIG) == []


def test_base_matrix_cell_becoming_skipped_fails_closed() -> None:
    base = {
        "routing_matrix": [
            {
                "backend": "fuzzy",
                "catalog_size": 100,
                "status": "ok",
                "recall_at_k": 0.5,
                "mrr": 0.4,
                "precision_at_k": 0.1,
            }
        ]
    }
    head = {
        "routing_matrix": [
            {"backend": "fuzzy", "catalog_size": 100, "status": "skipped: backend error"}
        ]
    }
    violations = evaluate_gate(base, head, _CONFIG)
    assert len(violations) == 3
    assert all(violation.head is None for violation in violations)


def test_token_savings_band_is_percent_points() -> None:
    base = {"context": [{"scenario": "s", "naive_delta": {"pct_reduction": 60.0}}]}
    within = {"context": [{"scenario": "s", "naive_delta": {"pct_reduction": 58.5}}]}  # -1.5pp
    beyond = {"context": [{"scenario": "s", "naive_delta": {"pct_reduction": 57.0}}]}  # -3.0pp
    assert evaluate_gate(base, within, _CONFIG) == []
    assert len(evaluate_gate(base, beyond, _CONFIG)) == 1


def test_compaction_ratio_uses_relative_percent_band() -> None:
    base = {"context": [{"scenario": "s", "avg_compaction_ratio": 2.0}]}
    within = {"context": [{"scenario": "s", "avg_compaction_ratio": 1.92}]}  # -4%
    beyond = {"context": [{"scenario": "s", "avg_compaction_ratio": 1.8}]}  # -10%
    assert evaluate_gate(base, within, _CONFIG) == []
    violation = evaluate_gate(base, beyond, _CONFIG)[0]
    assert violation.metric == "avg_compaction_ratio"
    assert violation.unit == "%"
    assert round(violation.regression or 0.0, 2) == 10.0


def test_committed_gating_config_loads() -> None:
    cfg = load_gating_config(Path(__file__).parent.parent / "benchmarks" / "gating.yaml")
    assert cfg.bands["recall_at_k"] == 1.0
    assert cfg.bands["token_savings_pct"] == 2.0
    assert cfg.bands["avg_compaction_ratio"] == 5.0
    assert cfg.override_label == "benchmark-accepted"


def test_config_disabling_all_gates_gates_nothing(tmp_path: Path) -> None:
    # A present config that disables every metric must be honored (gate nothing)
    # rather than silently reverting to DEFAULT_BANDS.
    cfg_path = tmp_path / "gating.yaml"
    cfg_path.write_text(
        "quality:\n"
        "  recall_at_k: { gating: false }\n"
        "  mrr: { gating: false }\n"
        "override_label: custom-accept\n",
        encoding="utf-8",
    )
    cfg = load_gating_config(cfg_path)
    assert cfg.bands == {}
    assert cfg.override_label == "custom-accept"
    # With no gated metrics, even a large drop cannot produce a violation.
    assert evaluate_gate(_routing(0.50), _routing(0.10), cfg) == []


def test_config_without_quality_block_keeps_defaults(tmp_path: Path) -> None:
    # An incomplete config (no ``quality`` block at all) keeps the safe defaults.
    cfg_path = tmp_path / "gating.yaml"
    cfg_path.write_text("latency:\n  gating: false\n", encoding="utf-8")
    cfg = load_gating_config(cfg_path)
    assert cfg.bands["recall_at_k"] == 1.0
    assert cfg.override_label == "benchmark-accepted"


def test_ci_reads_baseline_from_target_commit() -> None:
    root = Path(__file__).parent.parent
    workflow = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    comment_job = workflow.split("  benchmark-comment:", 1)[1].split("  benchmark-gate:", 1)[0]
    gate_job = workflow.split("  benchmark-gate:", 1)[1].split("  docs-build:", 1)[0]
    assert "github.event.pull_request.base.sha" in gate_job
    assert 'git show "${BASE_SHA}:benchmarks/results/latest.json"' in gate_job
    assert "github.event.pull_request.base.sha" in comment_job
    assert 'git show "${BASE_SHA}:benchmarks/results/latest.json"' in comment_job
    assert "cp benchmarks/results/latest.json benchmarks/results/base.json" not in workflow


def test_smoke_eval_is_gating_in_ci() -> None:
    root = Path(__file__).parent.parent
    workflow = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    smoke_step = workflow.split('- name: "Smoke evaluation', 1)[1].split("      - name:", 1)[0]
    assert "continue-on-error" not in smoke_step
    assert "python benchmarks/smoke_eval.py" in smoke_step


def test_cli_exit_codes(tmp_path: Path) -> None:
    base = tmp_path / "base.json"
    head = tmp_path / "head.json"
    base.write_text(json.dumps(_routing(0.50)), encoding="utf-8")
    head.write_text(json.dumps(_routing(0.45)), encoding="utf-8")  # -5pp
    config = str(Path(__file__).parent.parent / "benchmarks" / "gating.yaml")
    assert main(["--base", str(base), "--head", str(head), "--gating-config", config]) == 1
    # Override downgrades the failure to a warning.
    assert (
        main(["--base", str(base), "--head", str(head), "--gating-config", config, "--override"])
        == 0
    )
    # Clean head passes.
    head.write_text(json.dumps(_routing(0.50)), encoding="utf-8")
    assert main(["--base", str(base), "--head", str(head), "--gating-config", config]) == 0
