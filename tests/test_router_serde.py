"""Tests for ``RouteResult.to_dict`` / ``RouteResult.from_dict`` (issue #289).

Mirrors the round-trip style used in :mod:`tests.test_history` for
:class:`RouteHistory` and in :mod:`tests.test_router` for
:class:`RouteTrace`.  Pins both the full and ID-only payload shapes.
"""

from __future__ import annotations

import pytest

from contextweaver.routing.graph import ChoiceGraph
from contextweaver.routing.router import Router, RouteResult
from contextweaver.routing.trace import RouteTrace
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import SelectableItem

# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _item(iid: str, name: str = "", description: str = "desc") -> SelectableItem:
    return SelectableItem(id=iid, kind="tool", name=name or iid, description=description)


def _live_result(*, debug: bool = False) -> RouteResult:
    items = [
        _item("db_read", "read_db", "Read from database"),
        _item("db_write", "write_db", "Write to database"),
        _item("send_email", "send_email", "Send email notification"),
    ]
    graph: ChoiceGraph = TreeBuilder(max_children=20).build(items)
    router = Router(graph, items=items, beam_width=2, top_k=20, confidence_gap=0.15)
    return router.route("database read", debug=debug)


# ----------------------------------------------------------------------
# Round-trip
# ----------------------------------------------------------------------


def test_route_result_to_dict_round_trips_full_payload() -> None:
    original = _live_result(debug=True)
    restored = RouteResult.from_dict(original.to_dict())

    assert restored.candidate_ids == original.candidate_ids
    assert restored.scores == original.scores
    assert restored.paths == original.paths
    assert restored.is_ambiguous == original.is_ambiguous
    assert restored.clarifying_question == original.clarifying_question
    assert restored.excluded_count == original.excluded_count
    assert restored.gated_count == original.gated_count
    assert restored.context_hints == original.context_hints
    assert restored.context_boost_applied == original.context_boost_applied
    assert restored.history_adjustments == original.history_adjustments
    # Items round-trip via SelectableItem.to_dict / from_dict.
    assert len(restored.candidate_items) == len(original.candidate_items)
    for a, b in zip(restored.candidate_items, original.candidate_items, strict=True):
        assert a.id == b.id
        assert a.name == b.name
        assert a.description == b.description
    # Trace shape preserved (RouteTrace tested separately).
    assert restored.trace.query == original.trace.query
    assert restored.trace.confidence_gap == original.trace.confidence_gap
    assert len(restored.trace.steps) == len(original.trace.steps)


def test_route_result_to_dict_id_only_mode_omits_items() -> None:
    original = _live_result()
    payload = original.to_dict(include_items=False)
    assert "candidate_items" not in payload
    assert payload["candidate_ids"] == original.candidate_ids
    # Round-trips: items list is empty because they were intentionally not
    # serialised, but ids and scores survive.
    restored = RouteResult.from_dict(payload)
    assert restored.candidate_items == []
    assert restored.candidate_ids == original.candidate_ids
    assert restored.scores == original.scores


def test_route_result_to_dict_default_mode_includes_items() -> None:
    original = _live_result()
    payload = original.to_dict()
    assert "candidate_items" in payload
    assert len(payload["candidate_items"]) == len(original.candidate_items)
    assert payload["candidate_items"][0]["id"] == original.candidate_ids[0]


# ----------------------------------------------------------------------
# Field preservation
# ----------------------------------------------------------------------


def test_route_result_history_adjustments_preserved() -> None:
    """``history_adjustments`` is a dict[str, float] — must survive the wire."""
    original = RouteResult(
        candidate_ids=["a", "b"],
        scores=[0.6, 0.4],
        history_adjustments={"a": -0.12, "b": 0.03},
    )
    restored = RouteResult.from_dict(original.to_dict(include_items=False))
    assert restored.history_adjustments == {"a": -0.12, "b": 0.03}


def test_route_result_clarifying_question_round_trips_none() -> None:
    original = RouteResult(
        candidate_ids=["a"],
        scores=[0.9],
        is_ambiguous=False,
        clarifying_question=None,
    )
    restored = RouteResult.from_dict(original.to_dict(include_items=False))
    assert restored.clarifying_question is None


def test_route_result_clarifying_question_round_trips_value() -> None:
    original = RouteResult(
        candidate_ids=["a", "b"],
        scores=[0.51, 0.50],
        is_ambiguous=True,
        clarifying_question="Did you mean A or B?",
    )
    restored = RouteResult.from_dict(original.to_dict(include_items=False))
    assert restored.is_ambiguous is True
    assert restored.clarifying_question == "Did you mean A or B?"


# ----------------------------------------------------------------------
# Schema-extension tolerance
# ----------------------------------------------------------------------


def test_route_result_from_dict_missing_keys_use_defaults() -> None:
    """Older payloads round-trip cleanly.

    Mirrors the missing-key tolerance test for ``RouteHistory.from_dict``.
    """
    r = RouteResult.from_dict({"candidate_ids": ["x"], "scores": [0.5]})
    assert r.candidate_ids == ["x"]
    assert r.scores == [0.5]
    assert r.is_ambiguous is False
    assert r.context_hints == []
    assert r.history_adjustments == {}
    assert r.candidate_items == []
    assert isinstance(r.trace, RouteTrace)
    assert r.trace.query == ""


def test_route_result_from_dict_rejects_unparseable_score(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bad payloads raise rather than silently corrupt data."""
    _ = monkeypatch
    with pytest.raises((TypeError, ValueError)):
        RouteResult.from_dict({"candidate_ids": ["a"], "scores": ["not-a-float"]})


# ----------------------------------------------------------------------
# Determinism
# ----------------------------------------------------------------------


def test_route_result_to_dict_is_deterministic() -> None:
    """Two identical inputs produce byte-identical output."""
    r = _live_result()
    assert r.to_dict() == r.to_dict()
