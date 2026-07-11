"""Tests for contextweaver.extras.ranker (issue #388).

The sklearn-backed ``fit`` path is gated behind ``pytest.importorskip`` so
the suite passes on the default install; the pure-Python paths (featurize,
telemetry example derivation, JSON persistence, prediction over loaded
coefficients, evaluation) are exercised regardless.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import contextweaver.extras.ranker as ranker_mod
from contextweaver.diagnostics import DiagnosticEvent
from contextweaver.exceptions import ConfigError
from contextweaver.extras.ranker import (
    FEATURE_NAMES,
    RankingExample,
    ToolRanker,
    evaluate_ranker,
    examples_from_events,
    featurize,
)
from contextweaver.types import SelectableItem


def _item(
    iid: str,
    *,
    name: str | None = None,
    description: str = "generic description",
    tags: list[str] | None = None,
    namespace: str = "",
    args_schema: dict | None = None,
    side_effects: bool = False,
    cost_hint: float = 0.0,
) -> SelectableItem:
    return SelectableItem(
        id=iid,
        kind="tool",
        name=name if name is not None else iid,
        description=description,
        tags=tags or [],
        namespace=namespace,
        args_schema=args_schema or {},
        side_effects=side_effects,
        cost_hint=cost_hint,
    )


def _items_by_id() -> dict[str, SelectableItem]:
    items = [
        _item(
            "email.send",
            name="send email",
            description="Send an email message to a recipient",
            tags=["email", "comms"],
            namespace="comms",
            args_schema={"properties": {"to": {}, "subject": {}, "body": {}}},
            side_effects=True,
            cost_hint=0.2,
        ),
        _item(
            "billing.invoices",
            name="search invoices",
            description="Search customer invoices by amount and date",
            tags=["billing", "invoices"],
            namespace="billing",
        ),
        _item(
            "weather.forecast",
            name="weather forecast",
            description="Fetch the weather forecast for a city",
            tags=["weather"],
            namespace="weather",
        ),
    ]
    return {it.id: it for it in items}


def _browse(session: str, query: str | None, tool_ids: list[str]) -> DiagnosticEvent:
    attributes: dict = {"query_chars": len(query or ""), "tool_ids": tool_ids}
    if query is not None:
        attributes["query"] = query
    return DiagnosticEvent(event="browse.completed", session_id=session, attributes=attributes)


def _execute(
    session: str,
    tool_id: str,
    *,
    success: bool = True,
    duration_ms: float | None = 10.0,
) -> DiagnosticEvent:
    return DiagnosticEvent(
        event="execute.completed" if success else "execute.failed",
        session_id=session,
        tool_id=tool_id,
        success=success,
        duration_ms=duration_ms,
    )


# ---------------------------------------------------------------------------
# featurize
# ---------------------------------------------------------------------------


class TestFeaturize:
    def test_values_sane_and_deterministic(self) -> None:
        item = _items_by_id()["email.send"]
        feats = featurize("send an email", item)
        assert set(feats) == set(FEATURE_NAMES)
        assert feats == featurize("send an email", item)
        assert feats["jaccard"] > 0.0
        assert feats["name_exact"] == 1.0
        assert feats["tag_overlap"] > 0.0
        assert feats["schema_size"] == pytest.approx(0.3)
        assert feats["namespace_match"] == 0.0
        assert feats["side_effects"] == 1.0
        assert feats["cost_hint"] == pytest.approx(0.2)

    def test_no_overlap_features_zero(self) -> None:
        feats = featurize("zzzqqq", _items_by_id()["weather.forecast"])
        assert feats["jaccard"] == 0.0
        assert feats["name_exact"] == 0.0
        assert feats["tag_overlap"] == 0.0
        assert feats["namespace_match"] == 0.0

    def test_schema_size_capped(self) -> None:
        props = {f"p{i}": {} for i in range(25)}
        feats = featurize("q", _item("t", args_schema={"properties": props}))
        assert feats["schema_size"] == 1.0

    def test_malformed_schema_defensive(self) -> None:
        feats = featurize("q", _item("t", args_schema={"properties": "not-a-dict"}))
        assert feats["schema_size"] == 0.0


# ---------------------------------------------------------------------------
# examples_from_events
# ---------------------------------------------------------------------------


class TestExamplesFromEvents:
    def test_positive_and_negative_derivation(self) -> None:
        events = [
            _browse("s1", "send an email", ["email.send", "billing.invoices"]),
            _execute("s1", "email.send", success=True, duration_ms=12.5),
        ]
        examples = examples_from_events(events)
        assert [(e.tool_id, e.executed, e.success) for e in examples] == [
            ("billing.invoices", False, False),
            ("email.send", True, True),
        ]
        assert examples[0].latency_ms is None
        assert examples[1].latency_ms == 12.5
        assert all(e.query == "send an email" for e in examples)

    def test_failed_execution_is_executed_but_not_success(self) -> None:
        events = [
            _browse("s1", "search invoices", ["billing.invoices"]),
            _execute("s1", "billing.invoices", success=False),
        ]
        (example,) = examples_from_events(events)
        assert example.executed is True
        assert example.success is False

    def test_events_without_query_attribute_are_skipped(self) -> None:
        # The built-in GatewayTelemetry records only query_chars, never the
        # query text — such events must be skipped, not guessed at.
        events = [
            _browse("s1", None, ["email.send"]),
            _execute("s1", "email.send"),
        ]
        assert examples_from_events(events) == []

    def test_blank_query_skipped(self) -> None:
        events = [_browse("s1", "   ", ["email.send"])]
        assert examples_from_events(events) == []

    def test_window_closes_at_next_browse_in_session(self) -> None:
        events = [
            _browse("s1", "send an email", ["email.send"]),
            _browse("s1", "search invoices", ["billing.invoices"]),
            _execute("s1", "billing.invoices"),
        ]
        examples = examples_from_events(events)
        by_query = {(e.query, e.tool_id): e for e in examples}
        assert by_query[("send an email", "email.send")].executed is False
        assert by_query[("search invoices", "billing.invoices")].executed is True

    def test_executed_tool_outside_shortlist_is_positive(self) -> None:
        events = [
            _browse("s1", "send an email", ["billing.invoices"]),
            _execute("s1", "email.send"),
        ]
        examples = examples_from_events(events)
        assert [(e.tool_id, e.executed) for e in examples] == [
            ("billing.invoices", False),
            ("email.send", True),
        ]

    def test_sessions_are_isolated_and_sorted(self) -> None:
        events = [
            _browse("s2", "search invoices", ["billing.invoices"]),
            _browse("s1", "send an email", ["email.send"]),
            _execute("s2", "billing.invoices"),
            # s1's execute of a tool in another session must not leak into s2.
            _execute("s1", "email.send"),
        ]
        examples = examples_from_events(events)
        assert [(e.query, e.tool_id, e.executed) for e in examples] == [
            ("send an email", "email.send", True),
            ("search invoices", "billing.invoices", True),
        ]

    def test_malformed_tool_ids_defensive(self) -> None:
        events = [
            _browse("s1", "send an email", ["email.send", 42]),  # type: ignore[list-item]
            DiagnosticEvent(
                event="browse.completed",
                session_id="s2",
                attributes={"query": "x", "tool_ids": "not-a-list"},
            ),
        ]
        examples = examples_from_events(events)
        assert [e.tool_id for e in examples] == ["email.send"]

    def test_deterministic_output(self) -> None:
        events = [
            _browse("s1", "send an email", ["email.send", "billing.invoices"]),
            _execute("s1", "email.send"),
        ]
        assert examples_from_events(events) == examples_from_events(events)


class TestRankingExampleSerde:
    def test_round_trip(self) -> None:
        example = RankingExample(
            query="q",
            tool_id="t",
            executed=True,
            success=False,
            latency_ms=3.5,
            features={"jaccard": 0.5},
        )
        assert RankingExample.from_dict(example.to_dict()) == example

    def test_round_trip_defaults(self) -> None:
        example = RankingExample(query="q", tool_id="t", executed=False, success=False)
        assert RankingExample.from_dict(example.to_dict()) == example


# ---------------------------------------------------------------------------
# ToolRanker — sklearn-free paths
# ---------------------------------------------------------------------------


def _write_model(path: Path, *, coef: list[float] | None = None, version: int = 1) -> Path:
    # A hand-written coefficient file: weight only the jaccard feature.
    weights = coef if coef is not None else [1.0 if n == "jaccard" else 0.0 for n in FEATURE_NAMES]
    payload = {
        "version": version,
        "coef": weights,
        "intercept": 0.0,
        "feature_names": list(FEATURE_NAMES),
        "model_card": {"feature_names": list(FEATURE_NAMES)},
    }
    file = path / "ranker.json"
    file.write_text(json.dumps(payload), encoding="utf-8")
    return file


class TestToolRankerImportGuard:
    def test_instantiation_without_sklearn_raises_with_hint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ranker_mod, "LogisticRegression", None)
        with pytest.raises(ImportError, match=r"pip install 'contextweaver\[ranker\]'"):
            ToolRanker()

    def test_module_imports_without_sklearn(self) -> None:
        # The module under test was already imported at the top of this file;
        # its non-sklearn surface must be usable either way.
        assert callable(examples_from_events)
        assert callable(featurize)


@pytest.fixture
def loaded_ranker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ToolRanker:
    """A ranker usable without sklearn: guard bypassed, coefficients loaded."""
    monkeypatch.setattr(ranker_mod, "LogisticRegression", object())
    return ToolRanker.load(_write_model(tmp_path))


class TestToolRankerScoringWithoutSklearn:
    def test_predict_scores_and_rerank_deterministic(self, loaded_ranker: ToolRanker) -> None:
        items = sorted(_items_by_id().values(), key=lambda it: it.id)
        scores = loaded_ranker.predict_scores("send an email", items)
        assert set(scores) == {"billing.invoices", "email.send", "weather.forecast"}
        assert all(0.0 < s < 1.0 for s in scores.values())
        assert scores["email.send"] > scores["weather.forecast"]
        ranked = loaded_ranker.rerank("send an email", items)
        assert ranked[0].id == "email.send"
        assert ranked == loaded_ranker.rerank("send an email", items)

    def test_rerank_ties_break_by_id(self, loaded_ranker: ToolRanker) -> None:
        items = sorted(_items_by_id().values(), key=lambda it: it.id)
        # A query overlapping nothing scores every item identically.
        ranked = loaded_ranker.rerank("zzzqqq", items)
        assert [it.id for it in ranked] == ["billing.invoices", "email.send", "weather.forecast"]

    def test_save_load_round_trip_preserves_scores(
        self, loaded_ranker: ToolRanker, tmp_path: Path
    ) -> None:
        items = sorted(_items_by_id().values(), key=lambda it: it.id)
        out = tmp_path / "saved.json"
        loaded_ranker.save(out)
        restored = ToolRanker.load(out)
        query = "search customer invoices"
        assert restored.predict_scores(query, items) == loaded_ranker.predict_scores(query, items)
        assert restored.model_card == loaded_ranker.model_card

    def test_unfitted_ranker_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ranker_mod, "LogisticRegression", object())
        ranker = ToolRanker()
        with pytest.raises(ConfigError):
            ranker.predict_scores("q", [])
        with pytest.raises(ConfigError):
            ranker.save("unused.json")

    def test_load_rejects_malformed_files(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(ranker_mod, "LogisticRegression", object())
        with pytest.raises(ConfigError):
            ToolRanker.load(tmp_path / "missing.json")
        bad = tmp_path / "bad.json"
        bad.write_text("not json", encoding="utf-8")
        with pytest.raises(ConfigError):
            ToolRanker.load(bad)
        with pytest.raises(ConfigError):
            ToolRanker.load(_write_model(tmp_path, version=99))
        with pytest.raises(ConfigError):
            ToolRanker.load(_write_model(tmp_path, coef=[1.0]))

    def test_evaluate_ranker_reports_metrics(self, loaded_ranker: ToolRanker) -> None:
        events = [
            _browse("s1", "send an email", ["email.send", "billing.invoices"]),
            _execute("s1", "email.send"),
            _browse("s2", "search customer invoices", ["billing.invoices", "weather.forecast"]),
            _execute("s2", "billing.invoices"),
        ]
        examples = examples_from_events(events)
        report = evaluate_ranker(loaded_ranker, examples, _items_by_id(), k=2)
        assert set(report) == {
            "recall_at_k",
            "baseline_recall_at_k",
            "mrr",
            "baseline_mrr",
            "n_queries",
        }
        assert report["n_queries"] == 2.0
        # A jaccard-only coefficient model matches the lexical baseline.
        assert report["recall_at_k"] == report["baseline_recall_at_k"] == 1.0
        assert report["mrr"] == report["baseline_mrr"] == 1.0

    def test_evaluate_ranker_empty_examples(self, loaded_ranker: ToolRanker) -> None:
        report = evaluate_ranker(loaded_ranker, [], _items_by_id())
        assert report["n_queries"] == 0.0
        assert report["recall_at_k"] == 0.0


# ---------------------------------------------------------------------------
# ToolRanker — sklearn-backed training (skips cleanly when not installed)
# ---------------------------------------------------------------------------


def _training_setup() -> tuple[list[RankingExample], dict[str, SelectableItem]]:
    events = [
        _browse("s1", "send an email to a recipient", ["email.send", "billing.invoices"]),
        _execute("s1", "email.send"),
        _browse("s2", "search customer invoices", ["billing.invoices", "weather.forecast"]),
        _execute("s2", "billing.invoices"),
        _browse("s3", "weather forecast for a city", ["weather.forecast", "email.send"]),
        _execute("s3", "weather.forecast"),
    ]
    return examples_from_events(events), _items_by_id()


class TestToolRankerWithSklearn:
    def test_fit_predict_and_model_card(self) -> None:
        pytest.importorskip("sklearn")
        examples, items_by_id = _training_setup()
        ranker = ToolRanker()
        ranker.fit(examples, items_by_id)
        assert ranker.model_card["n_examples"] == len(examples)
        assert ranker.model_card["n_positive"] == 3
        assert ranker.model_card["feature_names"] == list(FEATURE_NAMES)
        assert "sklearn_version" in ranker.model_card
        # Featurize filled the examples in place.
        assert all(set(e.features) == set(FEATURE_NAMES) for e in examples)
        items = sorted(items_by_id.values(), key=lambda it: it.id)
        assert ranker.rerank("send an email to a recipient", items)[0].id == "email.send"

    def test_fit_is_deterministic(self) -> None:
        pytest.importorskip("sklearn")
        examples, items_by_id = _training_setup()
        ranker_a = ToolRanker()
        ranker_a.fit(examples, items_by_id)
        ranker_b = ToolRanker()
        ranker_b.fit(examples, items_by_id)
        items = sorted(items_by_id.values(), key=lambda it: it.id)
        query = "search customer invoices"
        assert ranker_a.predict_scores(query, items) == ranker_b.predict_scores(query, items)

    def test_save_load_round_trip_after_fit(self, tmp_path: Path) -> None:
        pytest.importorskip("sklearn")
        examples, items_by_id = _training_setup()
        ranker = ToolRanker()
        ranker.fit(examples, items_by_id)
        out = tmp_path / "trained.json"
        ranker.save(out)
        restored = ToolRanker.load(out)
        items = sorted(items_by_id.values(), key=lambda it: it.id)
        for query in ("send an email", "weather forecast"):
            assert restored.predict_scores(query, items) == ranker.predict_scores(query, items)
        # JSON persistence, never pickle.
        assert json.loads(out.read_text(encoding="utf-8"))["version"] == 1

    def test_fit_rejects_single_class_and_unknown_tools(self) -> None:
        pytest.importorskip("sklearn")
        items = _items_by_id()
        positives = [RankingExample(query="q", tool_id="email.send", executed=True, success=True)]
        with pytest.raises(ConfigError):
            ToolRanker().fit(positives, items)
        unknown = [RankingExample(query="q", tool_id="ghost", executed=True, success=True)]
        with pytest.raises(ConfigError):
            ToolRanker().fit(unknown, items)

    def test_evaluate_trained_ranker(self) -> None:
        pytest.importorskip("sklearn")
        examples, items_by_id = _training_setup()
        ranker = ToolRanker()
        ranker.fit(examples, items_by_id)
        report = evaluate_ranker(ranker, examples, items_by_id, k=2)
        assert report["n_queries"] == 3.0
        assert 0.0 <= report["recall_at_k"] <= 1.0
        assert 0.0 <= report["mrr"] <= 1.0
