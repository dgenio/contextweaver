"""Deterministic tests for the e2e quality + cost harness (issue #345).

These exercise the harness mechanics — prompt construction, token/cost
accounting, accuracy + hallucination scoring, and tolerant response parsing —
with controllable stand-in models. They never call a real model or network.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "benchmarks"))

import e2e_quality as e2e  # noqa: E402


def test_run_covers_three_strategies_with_stub() -> None:
    report = e2e.run()
    strategies = {r.strategy for r in report.results}
    assert strategies == {"naive", "competent", "contextweaver"}
    assert report.model == "stub"
    assert all(r.tasks_evaluated == len(e2e.load_tasks()) for r in report.results)


def test_token_reduction_holds_naive_competent_contextweaver() -> None:
    # The core premise: naive is the most expensive, the hand-built competent
    # baseline is much leaner, and contextweaver is no more expensive than the
    # competent baseline. This must hold regardless of the model used.
    report = e2e.run()
    by = {r.strategy: r for r in report.results}
    assert by["naive"].total_prompt_tokens > by["competent"].total_prompt_tokens
    assert by["contextweaver"].total_prompt_tokens <= by["competent"].total_prompt_tokens
    assert by["naive"].est_cost_usd > by["contextweaver"].est_cost_usd


def test_tool_and_answer_accuracy_with_oracle_model() -> None:
    # An oracle that always returns each task's gold tool + a matching answer
    # scores perfect tool/answer accuracy on every strategy. Hallucination is
    # 0 only for `naive`, which offers every catalog tool; a shortlisting
    # strategy that fails to surface a gold tool makes even the oracle's choice
    # an unavailable (hallucinated) call — see the offered-set test below.
    tasks = e2e.load_tasks()
    lookup = {t.query: t for t in tasks}

    def oracle(prompt: str) -> str:
        query = e2e._QUERY_RE.search(prompt).group(1)  # type: ignore[union-attr]
        task = lookup[query]
        return json.dumps({"tool": task.expected_tool, "answer": task.answer_contains})

    report = e2e.run(call_fn=oracle, model="oracle")
    for r in report.results:
        assert r.tool_accuracy == 1.0
        assert r.answer_accuracy == 1.0
    naive = next(r for r in report.results if r.strategy == "naive")
    assert naive.hallucination_rate == 0.0


def test_hallucination_rate_detects_unknown_tool() -> None:
    def liar(_prompt: str) -> str:
        return json.dumps({"tool": "made.up.tool", "answer": "nope"})

    report = e2e.run(call_fn=liar, model="liar")
    for r in report.results:
        assert r.hallucination_rate == 1.0
        assert r.tool_accuracy == 0.0


def test_hallucination_scored_against_offered_not_catalog() -> None:
    # billing.invoices.search is a real catalog tool, but here it is NOT in the
    # prompt's offered set. A model naming it must be flagged as a hallucinated
    # (unavailable) call even though the tool exists globally — tool accuracy
    # still credits the match against the gold tool.
    task = e2e.Task(
        query="search invoices",
        expected_tool="billing.invoices.search",
        answer_contains="invoice",
    )
    prompt = "Available tools:\ncomms.email.send — Send an email\n\nUser request: search invoices"
    offered = ["comms.email.send"]

    def picks_real_but_unoffered(_prompt: str) -> str:
        return json.dumps({"tool": "billing.invoices.search", "answer": "found the invoice"})

    result = e2e._score_strategy("t", [(prompt, offered)], [task], picks_real_but_unoffered, 1.0)
    assert result.hallucination_rate == 1.0
    assert result.tool_accuracy == 1.0


def test_cost_scales_linearly_with_price() -> None:
    cheap = {r.strategy: r for r in e2e.run(price_per_mtok=1.0).results}
    dear = {r.strategy: r for r in e2e.run(price_per_mtok=2.0).results}
    for strategy, r in cheap.items():
        if r.est_cost_usd:
            assert dear[strategy].est_cost_usd == pytest.approx(2.0 * r.est_cost_usd)


@pytest.mark.parametrize(
    ("raw", "tool", "answer"),
    [
        (
            '{"tool": "billing.payments.refund", "answer": "done"}',
            "billing.payments.refund",
            "done",
        ),
        ('prefix {"tool": "x.y", "answer": "a"} suffix', "x.y", "a"),
        ('{"tool": null, "answer": "none"}', None, "none"),
        ("not json at all", None, "not json at all"),
    ],
)
def test_parse_response_is_tolerant(raw: str, tool: str | None, answer: str) -> None:
    parsed = e2e._parse_response(raw)
    assert parsed.chosen_tool == tool
    assert parsed.answer == answer


def test_stub_selects_only_from_offered_tools() -> None:
    prompt = (
        "Available tools:\n"
        "billing.payments.refund — Refund a completed payment\n"
        "comms.email.send — Send an email message\n\n"
        "User request: refund a completed payment"
    )
    parsed = e2e._parse_response(e2e.stub_call_fn(prompt))
    assert parsed.chosen_tool == "billing.payments.refund"
