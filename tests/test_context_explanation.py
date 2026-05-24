"""Context-build explanation tests (issue #291).

Pins:

* Default ``explain=False`` returns a bare :class:`ContextPack`
  (existing behavior unchanged).
* Opt-in ``explain=True`` returns a ``(pack, explanation)`` tuple.
* The explanation captures sensitivity drops with the right reason.
* The explanation captures dedup drops with the right reason and
  preserves the dropped item's kind / sensitivity.
* The explanation captures dependency-closure additions.
* :class:`ContextBuildExplanation` round-trips through ``to_dict`` /
  ``from_dict``.
* The full normalised payload matches a checked-in golden fixture so
  drift is detected.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from contextweaver.config import ContextBudget, ContextPolicy
from contextweaver.context.explanation import (
    EXPLANATION_VERSION,
    CandidateExplanation,
    ContextBuildExplanation,
)
from contextweaver.context.manager import ContextManager
from contextweaver.envelope import ContextPack
from contextweaver.store.artifacts import InMemoryArtifactStore
from contextweaver.store.event_log import InMemoryEventLog
from contextweaver.types import ContextItem, ItemKind, Phase, Sensitivity
from tests.fixtures._normalize import load_fixture, normalize, to_canonical_json

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "context_explain"


def _basic_log() -> InMemoryEventLog:
    """The deterministic scenario captured in ``basic_build.json``."""
    log = InMemoryEventLog()
    log.append(ContextItem(id="u1", kind=ItemKind.user_turn, text="What is the weather?"))
    log.append(ContextItem(id="a1", kind=ItemKind.agent_msg, text="Let me check."))
    log.append(
        ContextItem(
            id="tc1",
            kind=ItemKind.tool_call,
            text="weather_api(city='London')",
            parent_id="u1",
        )
    )
    log.append(
        ContextItem(
            id="tr1",
            kind=ItemKind.tool_result,
            text="London: 15C",
            parent_id="tc1",
        )
    )
    # Two near-duplicate items so the dedup stage has work to do.
    log.append(ContextItem(id="dup1", kind=ItemKind.doc_snippet, text="Some doc snippet."))
    log.append(ContextItem(id="dup2", kind=ItemKind.doc_snippet, text="Some doc snippet."))
    log.append(
        ContextItem(
            id="conf1",
            kind=ItemKind.memory_fact,
            text="Confidential fact",
            sensitivity=Sensitivity.confidential,
        )
    )
    return log


def _basic_manager() -> ContextManager:
    return ContextManager(
        event_log=_basic_log(),
        artifact_store=InMemoryArtifactStore(),
        budget=ContextBudget(route=200, call=200, interpret=200, answer=400),
        policy=ContextPolicy(),
    )


# ----------------------------------------------------------------------
# Default surface unchanged
# ----------------------------------------------------------------------


def test_build_default_explain_off_returns_bare_pack() -> None:
    """The opt-in flag defaults to ``False``; existing callers see the
    same ``ContextPack`` return as before."""
    mgr = _basic_manager()
    pack = mgr.build_sync(phase=Phase.answer, query="weather")
    assert isinstance(pack, ContextPack)


def test_build_sync_with_explain_returns_tuple() -> None:
    mgr = _basic_manager()
    result = mgr.build_sync(phase=Phase.answer, query="weather", explain=True)
    assert isinstance(result, tuple)
    assert len(result) == 2
    pack, explanation = result
    assert isinstance(pack, ContextPack)
    assert isinstance(explanation, ContextBuildExplanation)


@pytest.mark.asyncio
async def test_build_async_with_explain_returns_tuple() -> None:
    mgr = _basic_manager()
    result = await mgr.build(phase=Phase.answer, query="weather", explain=True)
    assert isinstance(result, tuple)
    pack, explanation = result
    assert isinstance(pack, ContextPack)
    assert isinstance(explanation, ContextBuildExplanation)


# ----------------------------------------------------------------------
# Captures per-stage decisions
# ----------------------------------------------------------------------


def test_explanation_captures_sensitivity_drop() -> None:
    """A confidential item under the default floor lands in the
    explanation with ``drop_reason='sensitivity'``."""
    mgr = _basic_manager()
    _pack, ex = mgr.build_sync(phase=Phase.answer, query="weather", explain=True)
    assert isinstance(ex, ContextBuildExplanation)
    by_id = {c.item_id: c for c in ex.candidates}
    assert by_id["conf1"].drop_reason == "sensitivity"
    assert by_id["conf1"].included is False
    assert by_id["conf1"].sensitivity == "confidential"
    assert ex.sensitivity_drops == 1


def test_explanation_captures_dedup_drop_with_kind_preserved() -> None:
    """The dedup-collapsed item keeps its ``kind`` and ``sensitivity`` —
    the explanation captures them pre-dedup."""
    mgr = _basic_manager()
    _pack, ex = mgr.build_sync(phase=Phase.answer, query="weather", explain=True)
    assert isinstance(ex, ContextBuildExplanation)
    by_id = {c.item_id: c for c in ex.candidates}
    # One of the two dup_* items survives; the other is reported as dedup.
    survivors = {"dup1", "dup2"}
    dedup_ids = {
        c.item_id for c in ex.candidates if c.drop_reason == "dedup" and c.item_id in survivors
    }
    assert len(dedup_ids) == 1
    collapsed_id = next(iter(dedup_ids))
    assert by_id[collapsed_id].kind == "doc_snippet"
    assert by_id[collapsed_id].sensitivity == "public"
    assert by_id[collapsed_id].score is not None  # captured pre-dedup
    assert ex.dedup_removed == 1


def test_explanation_default_off_does_not_affect_pack() -> None:
    """The pipeline ``BuildStats`` produced when ``explain=True`` must
    be byte-identical to the ``explain=False`` run — opt-in
    instrumentation cannot change the answer."""
    mgr1 = _basic_manager()
    pack_off = mgr1.build_sync(phase=Phase.answer, query="weather")
    mgr2 = _basic_manager()
    pack_on, _ = mgr2.build_sync(phase=Phase.answer, query="weather", explain=True)
    assert pack_off.prompt == pack_on.prompt
    assert pack_off.stats.to_dict() == pack_on.stats.to_dict()


# ----------------------------------------------------------------------
# Serialisation
# ----------------------------------------------------------------------


def test_context_build_explanation_round_trips_through_dict() -> None:
    """``to_dict`` rounds scores to 4 decimals (same pattern as
    ``RouteTrace.to_dict``); the round-trip is therefore lossy on
    score precision by design — compare the canonical dict shape."""
    mgr = _basic_manager()
    _pack, ex = mgr.build_sync(phase=Phase.answer, query="weather", explain=True)
    assert isinstance(ex, ContextBuildExplanation)
    payload = ex.to_dict()
    restored = ContextBuildExplanation.from_dict(payload)
    assert restored.to_dict() == payload


def test_candidate_explanation_round_trips_through_dict() -> None:
    c = CandidateExplanation(
        item_id="x",
        kind="tool_result",
        sensitivity="public",
        score=0.4242,
        included=False,
        drop_reason="dedup",
        dependency_closure=True,
    )
    assert CandidateExplanation.from_dict(c.to_dict()) == c


def test_context_build_explanation_from_dict_supplies_defaults() -> None:
    """Older payloads — missing newer fields — round-trip cleanly."""
    payload = {"version": EXPLANATION_VERSION, "phase": "answer"}
    ex = ContextBuildExplanation.from_dict(payload)
    assert ex.phase == "answer"
    assert ex.candidates == []
    assert ex.included_count == 0


# ----------------------------------------------------------------------
# Golden fixture
# ----------------------------------------------------------------------


def test_explanation_matches_golden_fixture() -> None:
    mgr = _basic_manager()
    _pack, ex = mgr.build_sync(phase=Phase.answer, query="weather", explain=True)
    assert isinstance(ex, ContextBuildExplanation)

    actual = normalize(ex.to_dict())
    expected = normalize(load_fixture(FIXTURE_DIR / "basic_build.json"))
    if actual != expected:
        diff = (
            f"\n--- expected ({FIXTURE_DIR / 'basic_build.json'}):\n"
            + to_canonical_json(expected)
            + "\n--- actual:\n"
            + to_canonical_json(actual)
        )
        raise AssertionError(
            f"context-explain golden drifted: {FIXTURE_DIR / 'basic_build.json'}\n{diff}"
        )
