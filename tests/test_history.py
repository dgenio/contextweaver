"""Tests for contextweaver.routing.history (issue #27)."""

from __future__ import annotations

import pytest

from contextweaver.routing.history import (
    DEFAULT_DEPENDENCY_SATISFIED_BOOST,
    DEFAULT_DEPENDENCY_UNSATISFIED_PENALTY,
    DEFAULT_REPEAT_PENALTY,
    DEFAULT_RESULT_BOOST_WEIGHT,
    RouteHistory,
    adjust_scores,
)
from contextweaver.types import SelectableItem


def _item(iid: str, **kw: object) -> SelectableItem:
    return SelectableItem(
        id=iid,
        kind="tool",
        name=str(kw.get("name", iid)),
        description=str(kw.get("description", "desc")),
        depends_on=kw.get("depends_on"),  # type: ignore[arg-type]
        provides=kw.get("provides"),  # type: ignore[arg-type]
        requires=kw.get("requires"),  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# RouteHistory dataclass
# ---------------------------------------------------------------------------


def test_route_history_to_dict_round_trips() -> None:
    h = RouteHistory(
        called_tool_ids=["a", "b"],
        last_result_summary="found 3 results",
        step_number=3,
        repeat_penalty=0.4,
        result_boost_weight=0.2,
    )
    assert RouteHistory.from_dict(h.to_dict()) == h


def test_route_history_from_dict_supplies_defaults_for_missing_keys() -> None:
    """Old payloads (missing the tuning weights) must still deserialise."""
    h = RouteHistory.from_dict(
        {"called_tool_ids": ["a"], "last_result_summary": None, "step_number": 1}
    )
    assert h.repeat_penalty == DEFAULT_REPEAT_PENALTY
    assert h.result_boost_weight == DEFAULT_RESULT_BOOST_WEIGHT


def test_route_history_default_state_is_empty_step_one() -> None:
    h = RouteHistory()
    assert h.called_tool_ids == []
    assert h.last_result_summary is None
    assert h.step_number == 1


# ---------------------------------------------------------------------------
# adjust_scores — rule 1: repeat penalty
# ---------------------------------------------------------------------------


def test_adjust_scores_applies_repeat_penalty_to_called_tools() -> None:
    items = {"a": _item("a"), "b": _item("b")}
    history = RouteHistory(called_tool_ids=["a"], repeat_penalty=0.5)
    scored = [("a", 1.0), ("b", 1.0)]
    adjusted, deltas = adjust_scores(scored, history, items)
    by_id = dict(adjusted)
    assert by_id["a"] == 0.5  # 1.0 * 0.5
    assert by_id["b"] == 1.0
    assert deltas == {"a": -0.5}


def test_adjust_scores_rerank_demotes_repeated_tool_below_fresh_one() -> None:
    items = {"a": _item("a"), "b": _item("b")}
    history = RouteHistory(called_tool_ids=["a"], repeat_penalty=0.5)
    # ``a`` starts ahead — without history it would lead the ranking.
    scored = [("a", 0.9), ("b", 0.7)]
    adjusted, _ = adjust_scores(scored, history, items)
    # After the penalty: a=0.45, b=0.7 → b wins.
    assert adjusted[0] == ("b", 0.7)
    assert adjusted[1][0] == "a"


# ---------------------------------------------------------------------------
# adjust_scores — rule 2: result-summary boost
# ---------------------------------------------------------------------------


def test_adjust_scores_applies_result_summary_boost() -> None:
    items = {"a": _item("a"), "b": _item("b")}
    history = RouteHistory(last_result_summary="result", result_boost_weight=0.5)
    scored = [("a", 0.5), ("b", 0.5)]
    result_sim = {"a": 1.0, "b": 0.0}
    adjusted, deltas = adjust_scores(scored, history, items, result_similarity=result_sim)
    by_id = dict(adjusted)
    assert by_id["a"] == 0.5 + 0.5 * 1.0
    assert by_id["b"] == 0.5
    assert deltas == {"a": 0.5}


def test_adjust_scores_skips_result_boost_when_similarity_map_is_none() -> None:
    items = {"a": _item("a")}
    history = RouteHistory(last_result_summary=None)
    scored = [("a", 0.5)]
    adjusted, deltas = adjust_scores(scored, history, items, result_similarity=None)
    assert adjusted == [("a", 0.5)]
    assert deltas == {}


# ---------------------------------------------------------------------------
# adjust_scores — rule 3: dependency satisfied boost
# ---------------------------------------------------------------------------


def test_adjust_scores_boosts_when_requires_is_satisfied_by_provides_of_called() -> None:
    items = {
        "search_contacts": _item("search_contacts", provides=["contact_id"]),
        "send_email": _item("send_email", requires=["contact_id"]),
    }
    history = RouteHistory(called_tool_ids=["search_contacts"])
    scored = [("send_email", 0.5)]
    adjusted, deltas = adjust_scores(scored, history, items)
    assert adjusted[0][0] == "send_email"
    assert adjusted[0][1] == pytest.approx(0.5 + DEFAULT_DEPENDENCY_SATISFIED_BOOST)
    assert deltas["send_email"] == pytest.approx(DEFAULT_DEPENDENCY_SATISFIED_BOOST)


def test_adjust_scores_does_not_boost_when_requires_unsatisfied() -> None:
    items = {
        "send_email": _item("send_email", requires=["contact_id"]),
    }
    history = RouteHistory(called_tool_ids=[])  # no tools provide anything
    scored = [("send_email", 0.5)]
    adjusted, deltas = adjust_scores(scored, history, items)
    assert adjusted == [("send_email", 0.5)]
    assert deltas == {}


# ---------------------------------------------------------------------------
# adjust_scores — rule 4: dependency unsatisfied penalty
# ---------------------------------------------------------------------------


def test_adjust_scores_penalises_when_depends_on_references_uncalled_tool() -> None:
    items = {
        "send_email": _item("send_email", depends_on=["auth_login"]),
    }
    history = RouteHistory(called_tool_ids=["unrelated_tool"])
    scored = [("send_email", 0.5)]
    adjusted, deltas = adjust_scores(scored, history, items)
    assert adjusted[0][0] == "send_email"
    assert adjusted[0][1] == pytest.approx(0.5 - DEFAULT_DEPENDENCY_UNSATISFIED_PENALTY)
    assert deltas["send_email"] == pytest.approx(-DEFAULT_DEPENDENCY_UNSATISFIED_PENALTY)


def test_adjust_scores_does_not_penalise_when_depends_on_is_satisfied() -> None:
    items = {
        "send_email": _item("send_email", depends_on=["auth_login"]),
    }
    history = RouteHistory(called_tool_ids=["auth_login"])
    scored = [("send_email", 0.5)]
    adjusted, deltas = adjust_scores(scored, history, items)
    assert adjusted == [("send_email", 0.5)]
    assert deltas == {}


# ---------------------------------------------------------------------------
# Determinism + sort stability
# ---------------------------------------------------------------------------


def test_adjust_scores_resorts_by_negative_score_then_id() -> None:
    items = {"a": _item("a"), "b": _item("b"), "c": _item("c")}
    history = RouteHistory(called_tool_ids=["a"], repeat_penalty=0.5)
    scored = [("a", 1.0), ("b", 0.5), ("c", 0.5)]
    adjusted, _ = adjust_scores(scored, history, items)
    # After penalty: a=0.5, b=0.5, c=0.5 — three-way tie on score, id-asc breaks it.
    assert [iid for iid, _ in adjusted] == ["a", "b", "c"]


def test_adjust_scores_is_deterministic_across_calls() -> None:
    items = {"a": _item("a"), "b": _item("b")}
    history = RouteHistory(called_tool_ids=["a"])
    scored = [("a", 1.0), ("b", 0.5)]
    out_1, deltas_1 = adjust_scores(scored, history, items)
    out_2, deltas_2 = adjust_scores(scored, history, items)
    assert out_1 == out_2
    assert deltas_1 == deltas_2


def test_adjust_scores_tolerates_unknown_called_tool_ids() -> None:
    """called_tool_ids referencing items absent from the catalog must not error."""
    items = {"b": _item("b")}
    history = RouteHistory(called_tool_ids=["a_ghost"])  # not in items
    scored = [("b", 0.5)]
    adjusted, _ = adjust_scores(scored, history, items)
    assert adjusted == [("b", 0.5)]
