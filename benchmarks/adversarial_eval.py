#!/usr/bin/env python3
"""Adversarial comparative evaluation harness for ContextWeaver (#445).

The harness makes the competitive baseline explicit.  It never substitutes a
prompt simulation for provider-native Tool Search: without a real provider
runner those arms are reported as ``not_run`` and the report is not publishable.

Default execution is credential-free and exercises four offline arms with the
existing deterministic stub model:

1. naive all-tools/full-history control;
2. simple lexical retrieval + truncated history;
3. ContextWeaver routing only (full history retained);
4. ContextWeaver routing + budgeted context compilation.

Two additional arms require ``provider_native_fn`` supplied by an opt-in real
benchmark driver:

5. provider-native tool search/deferred loading over the full catalog;
6. ContextWeaver-bounded catalog + the same provider-native mechanism.

The real provider callback owns the provider API request and reports the actual
usage/latency/cost returned by that API.  This module owns scoring and the
anti-cherry-picking report shape.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(_ROOT / "src"))

import e2e_quality as legacy  # noqa: E402
from contextweaver.protocols import CharDivFourEstimator  # noqa: E402
from contextweaver.routing.router import Router  # noqa: E402
from contextweaver.routing.tree import TreeBuilder  # noqa: E402
from contextweaver.types import ContextItem, SelectableItem  # noqa: E402

ProviderNativeFn = Callable[
    [legacy.Task, list[SelectableItem], list[ContextItem]],
    "ProviderObservation",
]


@dataclass(frozen=True)
class ProviderObservation:
    """One real provider-native task observation returned by an external adapter."""

    chosen_tool: str | None
    answer: str
    prompt_tokens: int
    output_tokens: int = 0
    latency_ms: float = 0.0
    cost_usd: float = 0.0


@dataclass
class ArmResult:
    """Aggregate result for one benchmark arm, including explicit missing states."""

    arm: str
    category: str
    status: str
    tasks_evaluated: int = 0
    tool_accuracy: float | None = None
    hallucination_rate: float | None = None
    answer_accuracy: float | None = None
    avg_prompt_tokens: float | None = None
    total_prompt_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms_mean: float | None = None
    cost_usd: float | None = None
    reason: str | None = None


@dataclass
class AdversarialReport:
    """Six-arm comparison with methodology and publishability gates."""

    model: str
    dataset: str
    measurement_method: str
    results: list[ArmResult] = field(default_factory=list)
    contextweaver_losses: list[str] = field(default_factory=list)
    publishable: bool = False
    publishability_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "model": self.model,
            "dataset": self.dataset,
            "measurement_method": self.measurement_method,
            "results": [asdict(result) for result in self.results],
            "contextweaver_losses": list(self.contextweaver_losses),
            "publishable": self.publishable,
            "publishability_reasons": list(self.publishability_reasons),
        }


def _routing_only_prompt(
    task: legacy.Task,
    catalog: list[SelectableItem],
    history: list[ContextItem],
    router: Router,
) -> tuple[str, list[str]]:
    """Isolate ContextWeaver routing by keeping the same full history as naive."""
    result = router.route(task.query)
    by_id = {item.id: item for item in catalog}
    shortlist = [
        by_id[candidate_id]
        for candidate_id in result.candidate_ids[: legacy._CW_TOP_K]
        if candidate_id in by_id
    ]
    prompt = (
        f"{legacy._PREAMBLE}\n{legacy._render_history(history)}\n\n"
        f"{legacy._render_tools(shortlist)}\n\nUser request: {task.query}"
    )
    return prompt, [item.id for item in shortlist]


def _score_prompt_arm(
    arm: str,
    category: str,
    prompts: list[tuple[str, list[str]]],
    tasks: list[legacy.Task],
    call_fn: legacy.CallFn,
    price_per_mtok: float,
) -> ArmResult:
    estimator = CharDivFourEstimator()
    tool_hits = answer_hits = hallucinations = total_tokens = 0
    for (prompt, offered), task in zip(prompts, tasks, strict=True):
        offered_ids = set(offered)
        total_tokens += estimator.estimate(prompt)
        response = legacy._parse_response(call_fn(prompt))
        if response.chosen_tool == task.expected_tool:
            tool_hits += 1
        if response.chosen_tool is not None and response.chosen_tool not in offered_ids:
            hallucinations += 1
        if task.answer_contains.lower() in response.answer.lower():
            answer_hits += 1

    n = len(tasks)
    return ArmResult(
        arm=arm,
        category=category,
        status="complete",
        tasks_evaluated=n,
        tool_accuracy=round(tool_hits / n, 4) if n else 0.0,
        hallucination_rate=round(hallucinations / n, 4) if n else 0.0,
        answer_accuracy=round(answer_hits / n, 4) if n else 0.0,
        avg_prompt_tokens=round(total_tokens / n, 2) if n else 0.0,
        total_prompt_tokens=total_tokens,
        output_tokens=0,
        latency_ms_mean=None,
        cost_usd=round(total_tokens / 1_000_000 * price_per_mtok, 6),
    )


def _score_provider_arm(
    arm: str,
    category: str,
    tasks: list[legacy.Task],
    catalogs: list[list[SelectableItem]],
    history: list[ContextItem],
    provider_native_fn: ProviderNativeFn,
) -> ArmResult:
    tool_hits = answer_hits = hallucinations = prompt_tokens = output_tokens = 0
    total_latency = total_cost = 0.0
    for task, offered_catalog in zip(tasks, catalogs, strict=True):
        observation = provider_native_fn(task, offered_catalog, history)
        offered = {item.id for item in offered_catalog}
        if observation.chosen_tool == task.expected_tool:
            tool_hits += 1
        if observation.chosen_tool is not None and observation.chosen_tool not in offered:
            hallucinations += 1
        if task.answer_contains.lower() in observation.answer.lower():
            answer_hits += 1
        prompt_tokens += observation.prompt_tokens
        output_tokens += observation.output_tokens
        total_latency += observation.latency_ms
        total_cost += observation.cost_usd

    n = len(tasks)
    return ArmResult(
        arm=arm,
        category=category,
        status="complete",
        tasks_evaluated=n,
        tool_accuracy=round(tool_hits / n, 4) if n else 0.0,
        hallucination_rate=round(hallucinations / n, 4) if n else 0.0,
        answer_accuracy=round(answer_hits / n, 4) if n else 0.0,
        avg_prompt_tokens=round(prompt_tokens / n, 2) if n else 0.0,
        total_prompt_tokens=prompt_tokens,
        output_tokens=output_tokens,
        latency_ms_mean=round(total_latency / n, 2) if n else 0.0,
        cost_usd=round(total_cost, 6),
    )


def _not_run(arm: str, category: str, reason: str) -> ArmResult:
    return ArmResult(arm=arm, category=category, status="not_run", reason=reason)


def _metric(result: ArmResult, name: str) -> float | None:
    value = getattr(result, name)
    return float(value) if value is not None else None


def _find_losses(results: list[ArmResult]) -> list[str]:
    """Report negative comparisons instead of hiding them from the scorecard."""
    by_arm = {result.arm: result for result in results}
    losses: list[str] = []

    def compare(candidate: str, baseline: str) -> None:
        current = by_arm.get(candidate)
        reference = by_arm.get(baseline)
        if not current or not reference or current.status != "complete" or reference.status != "complete":
            return
        for metric in ("tool_accuracy", "answer_accuracy"):
            c_value = _metric(current, metric)
            b_value = _metric(reference, metric)
            if c_value is not None and b_value is not None and c_value < b_value:
                losses.append(f"{candidate} has lower {metric} than {baseline}: {c_value:.4f} < {b_value:.4f}")
        c_tokens = current.total_prompt_tokens
        b_tokens = reference.total_prompt_tokens
        if c_tokens is not None and b_tokens is not None and c_tokens > b_tokens:
            losses.append(f"{candidate} uses more prompt tokens than {baseline}: {c_tokens} > {b_tokens}")

    compare("contextweaver_routing", "simple_retrieval")
    compare("contextweaver_full", "simple_retrieval")
    compare("contextweaver_plus_native", "provider_native")
    return losses


def _publishability(model: str, results: list[ArmResult]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if model == "stub":
        reasons.append("stub model results are mechanics-only")
    by_arm = {result.arm: result for result in results}
    for arm in ("provider_native", "contextweaver_plus_native"):
        if by_arm[arm].status != "complete":
            reasons.append(f"{arm} was not run against a real provider-native mechanism")
    return not reasons, reasons


def run(
    call_fn: legacy.CallFn = legacy.stub_call_fn,
    *,
    model: str = "stub",
    price_per_mtok: float = legacy._DEFAULT_PRICE_PER_MTOK,
    tasks: list[legacy.Task] | None = None,
    provider_native_fn: ProviderNativeFn | None = None,
    dataset: str = "benchmarks/e2e/tasks.json (synthetic fixture)",
) -> AdversarialReport:
    """Run all available arms, explicitly preserving unavailable native arms."""
    tasks = tasks if tasks is not None else legacy.load_tasks()
    catalog = legacy._build_catalog()
    history = legacy._synthetic_history()
    router = Router(
        TreeBuilder().build(catalog),
        items=catalog,
        top_k=legacy._CW_TOP_K,
        beam_width=3,
    )

    prompt_arms = {
        "naive_control": (
            "historical_control",
            [legacy.build_naive_prompt(task, catalog, history) for task in tasks],
        ),
        "simple_retrieval": (
            "simple_baseline",
            [legacy.build_competent_prompt(task, catalog, history) for task in tasks],
        ),
        "contextweaver_routing": (
            "contextweaver_ablation",
            [_routing_only_prompt(task, catalog, history, router) for task in tasks],
        ),
        "contextweaver_full": (
            "contextweaver_full",
            [legacy.build_contextweaver_prompt(task, catalog, history, router) for task in tasks],
        ),
    }
    results = [
        _score_prompt_arm(arm, category, prompts, tasks, call_fn, price_per_mtok)
        for arm, (category, prompts) in prompt_arms.items()
    ]

    if provider_native_fn is None:
        reason = (
            "No provider_native_fn configured. Native Tool Search/deferred loading is not "
            "simulated with the stub; wire a real provider adapter to run this arm."
        )
        results.append(_not_run("provider_native", "strong_current_baseline", reason))
        results.append(_not_run("contextweaver_plus_native", "combined", reason))
    else:
        full_catalogs = [catalog for _ in tasks]
        results.append(
            _score_provider_arm(
                "provider_native",
                "strong_current_baseline",
                tasks,
                full_catalogs,
                history,
                provider_native_fn,
            )
        )
        by_id = {item.id: item for item in catalog}
        bounded_catalogs: list[list[SelectableItem]] = []
        for task in tasks:
            route = router.route(task.query)
            bounded_catalogs.append(
                [
                    by_id[candidate_id]
                    for candidate_id in route.candidate_ids[: legacy._CW_TOP_K]
                    if candidate_id in by_id
                ]
            )
        results.append(
            _score_provider_arm(
                "contextweaver_plus_native",
                "combined",
                tasks,
                bounded_catalogs,
                history,
                provider_native_fn,
            )
        )

    publishable, reasons = _publishability(model, results)
    return AdversarialReport(
        model=model,
        dataset=dataset,
        measurement_method="offline arms: heuristic/chardiv4; provider arms: provider-reported usage",
        results=results,
        contextweaver_losses=_find_losses(results),
        publishable=publishable,
        publishability_reasons=reasons,
    )


def render(report: AdversarialReport) -> str:
    lines = [
        "ContextWeaver adversarial comparative evaluation (#445)",
        "=" * 78,
        f"model={report.model}",
        f"dataset={report.dataset}",
        f"publishable={str(report.publishable).lower()}",
        "",
        f"{'arm':<28} {'status':<9} {'tool_acc':>8} {'ans_acc':>8} {'avg_tok':>9}",
    ]
    for result in report.results:
        if result.status != "complete":
            lines.append(f"{result.arm:<28} {result.status:<9} {'—':>8} {'—':>8} {'—':>9}")
            continue
        lines.append(
            f"{result.arm:<28} {result.status:<9} "
            f"{result.tool_accuracy or 0.0:>8.3f} {result.answer_accuracy or 0.0:>8.3f} "
            f"{result.avg_prompt_tokens or 0.0:>9.1f}"
        )

    lines.extend(["", "Where ContextWeaver lost:"])
    if report.contextweaver_losses:
        lines.extend(f"- {loss}" for loss in report.contextweaver_losses)
    else:
        lines.append("- No loss detected among the arms that actually ran. This is not proof of superiority.")

    if report.publishability_reasons:
        lines.extend(["", "Not publishable as competitive evidence:"])
        lines.extend(f"- {reason}" for reason in report.publishability_reasons)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-publishable", action="store_true")
    args = parser.parse_args(argv)

    report = run()
    print(render(report))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")
    if args.require_publishable and not report.publishable:
        print("error: report is mechanics-only; real provider-native arms are required", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
