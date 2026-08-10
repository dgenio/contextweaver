#!/usr/bin/env python3
"""Run the six-arm #445 evaluation against Anthropic's real Tool Search API.

Nothing runs without explicit credentials and an explicit model id. The output
keeps every trial, model/tool-search provenance, and the ContextWeaver-loss
section. Prices are caller-supplied because provider pricing changes over time;
zero means "cost not configured", not free.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_BENCHMARKS = Path(__file__).resolve().parent
sys.path.insert(0, str(_BENCHMARKS))
sys.path.insert(0, str(_BENCHMARKS.parent / "src"))

import adversarial_eval  # noqa: E402
from providers.anthropic_tool_search import (  # noqa: E402
    AnthropicToolSearchConfig,
    make_prompt_call_fn,
    make_provider_native_fn,
)

_PROVIDER_FEATURE = "Anthropic tool_search_tool_bm25_20251119"
_PROVIDER_DOC = "https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool"


def _metric_values(trials: list[dict[str, Any]], arm: str, metric: str) -> list[float]:
    values: list[float] = []
    for trial in trials:
        for result in trial["results"]:
            if result["arm"] == arm and result["status"] == "complete":
                value = result.get(metric)
                if isinstance(value, int | float):
                    values.append(float(value))
    return values


def _aggregate(trials: list[dict[str, Any]]) -> dict[str, Any]:
    arms = [result["arm"] for result in trials[0]["results"]]
    aggregate: dict[str, Any] = {}
    for arm in arms:
        arm_metrics: dict[str, Any] = {}
        for metric in (
            "tool_accuracy",
            "answer_accuracy",
            "hallucination_rate",
            "avg_prompt_tokens",
            "latency_ms_mean",
            "cost_usd",
        ):
            values = _metric_values(trials, arm, metric)
            if not values:
                arm_metrics[metric] = None
                continue
            arm_metrics[metric] = {
                "mean": round(statistics.fmean(values), 6),
                "min": round(min(values), 6),
                "max": round(max(values), 6),
                "stdev": round(statistics.stdev(values), 6) if len(values) > 1 else 0.0,
                "n": len(values),
            }
        aggregate[arm] = arm_metrics
    return aggregate


def run_trials(config: AnthropicToolSearchConfig, trials: int) -> dict[str, Any]:
    if trials < 3:
        raise ValueError("real comparative reports require at least 3 trials")

    prompt_call = make_prompt_call_fn(config)
    provider_native = make_provider_native_fn(config)
    raw_trials: list[dict[str, Any]] = []
    all_losses: list[str] = []
    for index in range(1, trials + 1):
        report = adversarial_eval.run(
            call_fn=prompt_call,
            model=f"anthropic/{config.model}",
            provider_native_fn=provider_native,
            dataset="benchmarks/e2e/tasks.json (synthetic fixture; replace for launch evidence)",
        )
        trial = report.to_dict()
        trial["trial"] = index
        raw_trials.append(trial)
        all_losses.extend(report.contextweaver_losses)

    configured_cost = config.input_usd_per_mtok > 0 or config.output_usd_per_mtok > 0
    result: dict[str, Any] = {
        "schema_version": 1,
        "run_at_utc": datetime.now(tz=UTC).isoformat(),
        "provider": "Anthropic",
        "model": config.model,
        "provider_feature": _PROVIDER_FEATURE,
        "provider_documentation": _PROVIDER_DOC,
        "tool_search_variant": "bm25",
        "trials": trials,
        "dataset": "benchmarks/e2e/tasks.json",
        "dataset_kind": "synthetic_fixture",
        "prices_configured": configured_cost,
        "input_usd_per_mtok": config.input_usd_per_mtok if configured_cost else None,
        "output_usd_per_mtok": config.output_usd_per_mtok if configured_cost else None,
        "raw_trials": raw_trials,
        "aggregate": _aggregate(raw_trials),
        "contextweaver_losses": sorted(set(all_losses)),
        # The API/mechanism is real, but the current in-tree task set is still a
        # synthetic fixture. This run is valid comparative engineering evidence,
        # not yet the final launch dataset required by #445/#621.
        "launch_publishable": False,
        "launch_blockers": [
            "current dataset is an in-tree synthetic fixture",
            "#445 requires held-out ambiguous/argument/large-result and external/adopter-derived cases",
        ],
    }
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--budget-usd", type=float, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        config = AnthropicToolSearchConfig.from_env()
        result = run_trials(config, args.trials)
    except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    spent = sum(
        float(arm.get("cost_usd") or 0.0)
        for trial in result["raw_trials"]
        for arm in trial["results"]
    )
    result["observed_cost_usd"] = round(spent, 6) if result["prices_configured"] else None
    if args.budget_usd is not None and result["prices_configured"] and spent > args.budget_usd:
        print(
            f"error: observed benchmark cost ${spent:.4f} exceeded --budget-usd ${args.budget_usd:.4f}",
            file=sys.stderr,
        )
        return 2

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"Wrote {args.output} with {args.trials} real Anthropic Tool Search trial(s). "
        "launch_publishable=false until external/held-out dataset gates are satisfied."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
