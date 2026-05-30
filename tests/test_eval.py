"""Tests for the evaluation harness (issue #12).

Metric math is verified against a deterministic ``_FakeRouter`` with
hand-constructed rankings so the asserted recall/MRR values are exact and
independent of the live scorer.  A second block exercises the real
``Router`` against the sample catalog to confirm the wiring holds.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from contextweaver.config import ContextBudget
from contextweaver.context.manager import ContextManager
from contextweaver.eval.context import ContextEvalReport, evaluate_context
from contextweaver.eval.dataset import EvalCase, EvalDataset
from contextweaver.eval.routing import RoutingEvalReport, evaluate_routing
from contextweaver.exceptions import ConfigError
from contextweaver.protocols import CharDivFourEstimator
from contextweaver.routing.catalog import generate_sample_catalog, load_catalog_dicts
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.store.event_log import InMemoryEventLog
from contextweaver.types import ContextItem, ItemKind, Phase

# ------------------------------------------------------------------
# Fake router for exact metric-math assertions
# ------------------------------------------------------------------


class _FakeRouter:
    """Returns a pre-canned ranking per query so metrics are deterministic."""

    def __init__(self, table: dict[str, tuple[list[str], list[float], int]]) -> None:
        self._table = table

    def route(self, query: str, *, debug: bool = False) -> SimpleNamespace:
        ids, scores, steps = self._table[query]
        return SimpleNamespace(
            candidate_ids=list(ids),
            scores=list(scores),
            trace=SimpleNamespace(steps=list(range(steps))),
        )


# ------------------------------------------------------------------
# EvalCase / EvalDataset
# ------------------------------------------------------------------


def test_eval_case_round_trip() -> None:
    case = EvalCase(query="send email", expected=["email.send"], tags=["comms"], namespace="comms")
    assert EvalCase.from_dict(case.to_dict()) == case


def test_eval_case_rejects_blank_query() -> None:
    with pytest.raises(ConfigError):
        EvalCase.from_dict({"query": "  ", "expected": ["x"]})


def test_eval_case_rejects_non_string_expected() -> None:
    with pytest.raises(ConfigError):
        EvalCase.from_dict({"query": "q", "expected": [1, 2]})


def test_eval_dataset_round_trip() -> None:
    ds = EvalDataset(
        cases=[
            EvalCase(query="a", expected=["t.a"]),
            EvalCase(query="b", expected=["t.b"]),
        ]
    )
    assert EvalDataset.from_dict(ds.to_dict()) == ds
    assert len(ds) == 2


def test_eval_dataset_load(tmp_path: Path) -> None:
    path = tmp_path / "ds.json"
    path.write_text(
        '[{"query": "send email", "expected": ["email.send"], "namespace": "comms"}]',
        encoding="utf-8",
    )
    ds = EvalDataset.load(path)
    assert len(ds) == 1
    assert ds.cases[0].expected == ["email.send"]


def test_eval_dataset_load_rejects_non_array(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text('{"query": "x"}', encoding="utf-8")
    with pytest.raises(ConfigError):
        EvalDataset.load(path)


# ------------------------------------------------------------------
# evaluate_routing — exact metric math
# ------------------------------------------------------------------


def test_evaluate_routing_exact_metrics() -> None:
    # q1: expected at rank 3 -> hit@1 miss, hit@3/5 hit, rr=1/3.
    # q2: expected at rank 1 -> all hits, rr=1.
    router = _FakeRouter(
        {
            "q1": (["a", "b", "c", "d", "e"], [0.9, 0.8, 0.7, 0.6, 0.5], 4),
            "q2": (["x", "a", "b", "c", "d"], [0.9, 0.2, 0.1, 0.05, 0.0], 2),
        }
    )
    ds = EvalDataset(
        cases=[
            EvalCase(query="q1", expected=["c"]),
            EvalCase(query="q2", expected=["x"]),
        ]
    )
    report = evaluate_routing(router, ds)  # type: ignore[arg-type]

    assert report.queries_evaluated == 2
    assert report.queries_skipped == 0
    assert report.top_1_recall == 0.5
    assert report.top_3_recall == 1.0
    assert report.top_5_recall == 1.0
    assert report.mrr == round((1 / 3 + 1.0) / 2, 4)
    assert report.avg_candidates == 5.0
    # confidence gap: q1=0.1, q2=0.7 -> mean 0.4
    assert report.avg_confidence_gap == 0.4
    # beam steps: q1=4, q2=2 -> mean 3.0
    assert report.avg_beam_steps == 3.0


def test_evaluate_routing_skips_unreachable_expected() -> None:
    router = _FakeRouter({"q1": (["a", "b"], [0.5, 0.4], 1)})
    ds = EvalDataset(cases=[EvalCase(query="q1", expected=["not-in-catalog"])])
    report = evaluate_routing(router, ds, catalog_ids={"a", "b"})  # type: ignore[arg-type]
    assert report.queries_evaluated == 0
    assert report.queries_skipped == 1
    assert report.mrr == 0.0


def test_routing_report_round_trip_and_summary() -> None:
    report = RoutingEvalReport(queries_evaluated=3, top_1_recall=0.5, mrr=0.6)
    assert RoutingEvalReport.from_dict(report.to_dict()) == report
    assert "recall@1=0.5000" in report.summary()


def test_evaluate_routing_real_router_sample_catalog() -> None:
    items = load_catalog_dicts(generate_sample_catalog(n=80, seed=42))
    graph = TreeBuilder().build(items)
    router = Router(graph, items=items, top_k=10, beam_width=3)
    ds = EvalDataset(
        cases=[
            EvalCase(query="send an email message", expected=["comms.email.send"]),
            EvalCase(query="export the audit log", expected=["admin.audit.export"]),
        ]
    )
    report = evaluate_routing(router, ds, catalog_ids={it.id for it in items})
    assert report.queries_evaluated == 2
    # Recall is monotonic in k and bounded.
    assert 0.0 <= report.top_1_recall <= report.top_3_recall <= report.top_5_recall <= 1.0


# ------------------------------------------------------------------
# evaluate_context
# ------------------------------------------------------------------


def _manager_with_events(events: list[ContextItem]) -> ContextManager:
    log = InMemoryEventLog()
    for ev in events:
        log.append(ev)
    budget = ContextBudget(route=2000, call=4000, interpret=4000, answer=6000)
    return ContextManager(event_log=log, budget=budget, estimator=CharDivFourEstimator())


def test_evaluate_context_metrics_exact() -> None:
    events = [
        ContextItem(id="u1", kind=ItemKind.user_turn, text="Find unpaid invoices for ACME"),
        ContextItem(id="a1", kind=ItemKind.agent_msg, text="Searching invoices now."),
        ContextItem(id="u2", kind=ItemKind.user_turn, text="Then email a reminder"),
    ]
    mgr = _manager_with_events(events)
    report = evaluate_context(mgr, phase=Phase.answer, query="email a reminder")

    assert report.phase == "answer"
    assert report.budget_tokens == 6000
    expected_naive = CharDivFourEstimator().estimate("\n".join(e.text for e in events))
    assert report.naive_tokens == expected_naive
    assert report.token_savings == report.naive_tokens - report.prompt_tokens
    assert report.prompt_tokens > 0


def test_context_report_round_trip_and_summary() -> None:
    report = ContextEvalReport(phase="answer", prompt_tokens=10, budget_tokens=100)
    assert ContextEvalReport.from_dict(report.to_dict()) == report
    assert "phase=answer" in report.summary()
