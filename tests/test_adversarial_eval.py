"""Deterministic tests for the adversarial comparative evaluation harness (#445)."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "benchmarks"))

import adversarial_eval as adversarial  # noqa: E402
import e2e_quality as legacy  # noqa: E402


def test_default_report_names_all_six_required_arms() -> None:
    report = adversarial.run()
    by_arm = {result.arm: result for result in report.results}
    assert set(by_arm) == {
        "naive_control",
        "simple_retrieval",
        "contextweaver_routing",
        "contextweaver_full",
        "provider_native",
        "contextweaver_plus_native",
    }
    assert by_arm["naive_control"].category == "historical_control"
    assert by_arm["provider_native"].category == "strong_current_baseline"


def test_provider_native_is_not_faked_by_stub() -> None:
    report = adversarial.run()
    by_arm = {result.arm: result for result in report.results}
    assert by_arm["provider_native"].status == "not_run"
    assert by_arm["provider_native"].tool_accuracy is None
    assert by_arm["contextweaver_plus_native"].status == "not_run"
    assert "not simulated" in (by_arm["provider_native"].reason or "").lower()
    assert report.publishable is False
    assert any("provider_native" in reason for reason in report.publishability_reasons)


def test_offline_ablation_arms_run_with_same_task_set() -> None:
    report = adversarial.run()
    task_count = len(legacy.load_tasks())
    by_arm = {result.arm: result for result in report.results}
    for arm in (
        "naive_control",
        "simple_retrieval",
        "contextweaver_routing",
        "contextweaver_full",
    ):
        assert by_arm[arm].status == "complete"
        assert by_arm[arm].tasks_evaluated == task_count


def test_routing_only_is_distinct_from_full_context_compilation() -> None:
    report = adversarial.run()
    by_arm = {result.arm: result for result in report.results}
    # Both use the same ContextWeaver shortlist. Full adds the budgeted context
    # compiler; routing-only deliberately keeps the full synthetic history.
    assert by_arm["contextweaver_routing"].total_prompt_tokens is not None
    assert by_arm["contextweaver_full"].total_prompt_tokens is not None
    assert (
        by_arm["contextweaver_routing"].total_prompt_tokens
        != by_arm["contextweaver_full"].total_prompt_tokens
    )


def test_real_provider_callback_populates_native_and_combined_arms() -> None:
    def provider(
        task: legacy.Task,
        offered: list[legacy.SelectableItem],
        _history: list[legacy.ContextItem],
    ) -> adversarial.ProviderObservation:
        ids = {item.id for item in offered}
        chosen = task.expected_tool if task.expected_tool in ids else None
        return adversarial.ProviderObservation(
            chosen_tool=chosen,
            answer=task.answer_contains if chosen else "not available",
            prompt_tokens=123,
            output_tokens=7,
            latency_ms=25.0,
            cost_usd=0.001,
        )

    report = adversarial.run(
        call_fn=legacy.stub_call_fn,
        model="real-provider-test-double",
        provider_native_fn=provider,
    )
    by_arm = {result.arm: result for result in report.results}
    assert by_arm["provider_native"].status == "complete"
    assert by_arm["provider_native"].tool_accuracy == 1.0
    assert by_arm["provider_native"].total_prompt_tokens == 123 * len(legacy.load_tasks())
    assert by_arm["contextweaver_plus_native"].status == "complete"
    assert report.publishable is True


def test_stub_model_never_becomes_publishable_even_with_provider_test_double() -> None:
    def provider(
        _task: legacy.Task,
        offered: list[legacy.SelectableItem],
        _history: list[legacy.ContextItem],
    ) -> adversarial.ProviderObservation:
        return adversarial.ProviderObservation(
            chosen_tool=offered[0].id if offered else None,
            answer="synthetic",
            prompt_tokens=10,
        )

    report = adversarial.run(provider_native_fn=provider)
    assert report.publishable is False
    assert "stub model results are mechanics-only" in report.publishability_reasons


def test_report_exposes_where_contextweaver_lost() -> None:
    baseline = adversarial.ArmResult(
        arm="simple_retrieval",
        category="simple_baseline",
        status="complete",
        tool_accuracy=1.0,
        answer_accuracy=1.0,
        total_prompt_tokens=100,
    )
    cw = adversarial.ArmResult(
        arm="contextweaver_full",
        category="contextweaver_full",
        status="complete",
        tool_accuracy=0.5,
        answer_accuracy=0.75,
        total_prompt_tokens=120,
    )
    losses = adversarial._find_losses([baseline, cw])
    assert any("lower tool_accuracy" in loss for loss in losses)
    assert any("lower answer_accuracy" in loss for loss in losses)
    assert any("more prompt tokens" in loss for loss in losses)


def test_render_calls_out_non_publishable_mechanics() -> None:
    rendered = adversarial.render(adversarial.run())
    assert "Where ContextWeaver lost:" in rendered
    assert "Not publishable as competitive evidence:" in rendered
    assert "provider_native" in rendered
