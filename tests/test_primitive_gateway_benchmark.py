"""Tests for the mixed-primitive gateway benchmark harness (#673)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_BENCH = Path(__file__).resolve().parent.parent / "benchmarks" / "primitive_gateway_benchmark.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("primitive_gateway_benchmark", _BENCH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Register before exec so @dataclass can resolve the module via sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_run_all_reports_per_kind_savings() -> None:
    report = _load().run_all(n_resources=20, n_prompts=12, top_k=5)
    kinds = {k["kind"] for k in report["per_kind"]}
    assert kinds == {"resource", "prompt"}
    for kind in report["per_kind"]:
        # The bounded browse surface is strictly smaller than the full listing.
        assert kind["browse_tokens"] < kind["naive_tokens"]
        assert kind["savings_pct"] > 0
        assert 0.0 <= kind["recall_at_k"] <= 1.0
    assert report["overall_savings_pct"] > 0


def test_run_all_recall_finds_expected_primitive() -> None:
    report = _load().run_all(n_resources=20, n_prompts=12, top_k=8)
    by_kind = {k["kind"]: k for k in report["per_kind"]}
    # The seeded queries target a 'config' resource and a 'summarize' prompt.
    assert by_kind["resource"]["recall_at_k"] == 1.0
    assert by_kind["prompt"]["recall_at_k"] == 1.0
