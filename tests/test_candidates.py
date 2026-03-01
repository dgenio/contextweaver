"""Tests for contextweaver.context.candidates."""

from __future__ import annotations

from contextweaver.config import ContextPolicy
from contextweaver.context.candidates import generate_candidates, resolve_dependency_closure
from contextweaver.store.event_log import InMemoryEventLog
from contextweaver.types import ContextItem, ItemKind, Phase


def _item(
    iid: str, kind: ItemKind = ItemKind.user_turn, parent_id: str | None = None
) -> ContextItem:
    return ContextItem(id=iid, kind=kind, text=f"text {iid}", parent_id=parent_id)


def test_generate_candidates_filters_by_phase() -> None:
    log = InMemoryEventLog()
    log.append(_item("u1", ItemKind.user_turn))
    log.append(_item("r1", ItemKind.tool_result))
    policy = ContextPolicy()
    # route phase: only user_turn, plan_state, policy allowed
    candidates = generate_candidates(log, Phase.route, policy)
    kinds = {c.kind for c in candidates}
    assert ItemKind.tool_result not in kinds
    assert ItemKind.user_turn in kinds


def test_generate_candidates_answer_phase_all_kinds() -> None:
    log = InMemoryEventLog()
    for kind in ItemKind:
        log.append(_item(f"item_{kind.value}", kind))
    policy = ContextPolicy()
    candidates = generate_candidates(log, Phase.answer, policy)
    assert len(candidates) == len(list(ItemKind))


def test_resolve_dependency_closure() -> None:
    log = InMemoryEventLog()
    log.append(_item("parent1", ItemKind.tool_call))
    log.append(_item("child1", ItemKind.tool_result, parent_id="parent1"))
    # Start with only child1
    child = [c for c in log.all() if c.id == "child1"]
    expanded, closures = resolve_dependency_closure(child, log)
    ids = {item.id for item in expanded}
    assert "parent1" in ids
    assert closures == 1


def test_closure_no_duplicates() -> None:
    log = InMemoryEventLog()
    log.append(_item("p1", ItemKind.tool_call))
    log.append(_item("c1", ItemKind.tool_result, parent_id="p1"))
    items = log.all()  # both p1 and c1 already present
    expanded, closures = resolve_dependency_closure(items, log)
    ids = [item.id for item in expanded]
    assert ids.count("p1") == 1
    assert closures == 0
