"""Tests for contextweaver.context.selection -- budget packing, dependency closure, reasons."""

from __future__ import annotations

import asyncio

from contextweaver.context.selection import select_and_pack
from contextweaver.store.event_log import InMemoryEventLog
from contextweaver.types import ContextItem, ItemKind


def _item(
    iid: str,
    kind: ItemKind = ItemKind.USER_TURN,
    tokens: int = 100,
    parent_id: str | None = None,
) -> ContextItem:
    return ContextItem(
        id=iid, kind=kind, text="x" * (tokens * 4), token_estimate=tokens, parent_id=parent_id
    )


class TestSelectAndPack:
    """Tests for select_and_pack."""

    def test_within_budget(self) -> None:
        event_log = InMemoryEventLog()
        scored = [(_item(f"i{i}", tokens=100), 1.0 - i * 0.1) for i in range(3)]
        selected, excluded, closures = select_and_pack(scored, 500, event_log)
        total_tokens = sum(item.token_estimate for item in selected)
        assert total_tokens <= 500
        assert len(selected) + len(excluded) == 3

    def test_budget_exceeded_drops_items(self) -> None:
        event_log = InMemoryEventLog()
        # Each item is 200 tokens, budget is 500 so only 2 can fit
        scored = [(_item(f"i{i}", tokens=200), 1.0 - i * 0.1) for i in range(5)]
        selected, excluded, _ = select_and_pack(scored, 500, event_log)
        assert len(selected) == 2
        assert len(excluded) == 3

    def test_dependency_closure_includes_parent(self) -> None:
        event_log = InMemoryEventLog()
        parent = _item("parent1", ItemKind.TOOL_CALL, tokens=50)
        child = _item("child1", ItemKind.TOOL_RESULT, tokens=50, parent_id="parent1")
        asyncio.run(event_log.append(parent))

        scored = [(child, 1.0)]
        selected, excluded, closures = select_and_pack(scored, 200, event_log)
        selected_ids = {item.id for item in selected}
        assert "parent1" in selected_ids
        assert "child1" in selected_ids
        assert closures == 1

    def test_dependency_closure_budget_exceeded(self) -> None:
        event_log = InMemoryEventLog()
        parent = _item("parent1", ItemKind.TOOL_CALL, tokens=100)
        child = _item("child1", ItemKind.TOOL_RESULT, tokens=100, parent_id="parent1")
        asyncio.run(event_log.append(parent))

        # Budget too small for both parent + child
        scored = [(child, 1.0)]
        selected, excluded, closures = select_and_pack(scored, 100, event_log)
        assert len(selected) == 0
        assert len(excluded) == 1
        assert excluded[0] == ("child1", "dependency_closure_budget")

    def test_excluded_reasons_populated(self) -> None:
        event_log = InMemoryEventLog()
        scored = [
            (_item("i1", tokens=100), 1.0),
            (_item("i2", tokens=100), 0.9),
            (_item("i3", tokens=100), 0.8),
            (_item("i4", tokens=100), 0.7),
        ]
        selected, excluded, _ = select_and_pack(scored, 250, event_log)
        assert len(excluded) > 0
        reasons = {r for _, r in excluded}
        assert reasons.issubset({"budget", "dependency_closure_budget", "lower_score"})
