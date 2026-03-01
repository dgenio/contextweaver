"""Tests for contextweaver.store.event_log -- async append/query/get/children/parent/count."""

from __future__ import annotations

import pytest

from contextweaver.exceptions import ItemNotFoundError
from contextweaver.store.event_log import InMemoryEventLog
from contextweaver.types import ContextItem, ItemKind


def _make_item(
    iid: str,
    kind: ItemKind = ItemKind.USER_TURN,
    text: str = "text",
    timestamp: float = 0.0,
    parent_id: str | None = None,
) -> ContextItem:
    return ContextItem(
        id=iid,
        kind=kind,
        text=text,
        token_estimate=len(text) // 4,
        metadata={"timestamp": timestamp},
        parent_id=parent_id,
    )


class TestInMemoryEventLog:
    """Tests for InMemoryEventLog async methods."""

    async def test_append_and_get(self, event_log: InMemoryEventLog) -> None:
        item = _make_item("i1")
        await event_log.append(item)
        result = await event_log.get("i1")
        assert result.text == "text"

    async def test_duplicate_raises(self, event_log: InMemoryEventLog) -> None:
        await event_log.append(_make_item("i1"))
        with pytest.raises(ValueError, match="Duplicate"):
            await event_log.append(_make_item("i1"))

    async def test_get_missing_raises(self, event_log: InMemoryEventLog) -> None:
        with pytest.raises(ItemNotFoundError):
            await event_log.get("missing")

    async def test_count(self, event_log: InMemoryEventLog) -> None:
        assert await event_log.count() == 0
        await event_log.append(_make_item("i1"))
        await event_log.append(_make_item("i2"))
        assert await event_log.count() == 2

    async def test_len(self, event_log: InMemoryEventLog) -> None:
        assert len(event_log) == 0
        await event_log.append(_make_item("i1"))
        assert len(event_log) == 1

    async def test_query_all(self, event_log: InMemoryEventLog) -> None:
        for i in range(5):
            await event_log.append(_make_item(f"i{i}"))
        result = await event_log.query()
        assert len(result) == 5

    async def test_query_filter_by_kind(self, event_log: InMemoryEventLog) -> None:
        await event_log.append(_make_item("u1", ItemKind.USER_TURN))
        await event_log.append(_make_item("t1", ItemKind.TOOL_CALL))
        await event_log.append(_make_item("u2", ItemKind.USER_TURN))
        result = await event_log.query(kinds=[ItemKind.USER_TURN])
        assert len(result) == 2
        assert all(r.kind == ItemKind.USER_TURN for r in result)

    async def test_query_since_timestamp(self, event_log: InMemoryEventLog) -> None:
        await event_log.append(_make_item("i1", timestamp=100.0))
        await event_log.append(_make_item("i2", timestamp=200.0))
        await event_log.append(_make_item("i3", timestamp=300.0))
        result = await event_log.query(since=200.0)
        assert len(result) == 2
        assert result[0].id == "i2"

    async def test_query_limit(self, event_log: InMemoryEventLog) -> None:
        for i in range(10):
            await event_log.append(_make_item(f"i{i}"))
        result = await event_log.query(limit=3)
        assert len(result) == 3

    async def test_children(self, event_log: InMemoryEventLog) -> None:
        await event_log.append(_make_item("tc1", ItemKind.TOOL_CALL))
        await event_log.append(_make_item("tr1", ItemKind.TOOL_RESULT, parent_id="tc1"))
        await event_log.append(_make_item("tr2", ItemKind.TOOL_RESULT, parent_id="tc1"))
        await event_log.append(_make_item("other"))
        children = await event_log.children("tc1")
        assert len(children) == 2
        assert all(c.parent_id == "tc1" for c in children)

    async def test_parent(self, event_log: InMemoryEventLog) -> None:
        await event_log.append(_make_item("tc1", ItemKind.TOOL_CALL))
        await event_log.append(_make_item("tr1", ItemKind.TOOL_RESULT, parent_id="tc1"))
        parent = await event_log.parent("tr1")
        assert parent is not None
        assert parent.id == "tc1"

    async def test_parent_returns_none_for_root(self, event_log: InMemoryEventLog) -> None:
        await event_log.append(_make_item("i1"))
        parent = await event_log.parent("i1")
        assert parent is None

    async def test_all_sync(self, event_log: InMemoryEventLog) -> None:
        await event_log.append(_make_item("i1"))
        await event_log.append(_make_item("i2"))
        items = event_log.all_sync()
        assert len(items) == 2

    async def test_roundtrip(self, event_log: InMemoryEventLog) -> None:
        await event_log.append(_make_item("i1", ItemKind.AGENT_MSG, "hello"))
        data = event_log.to_dict()
        restored = InMemoryEventLog.from_dict(data)
        assert len(restored) == 1
        assert restored.get_sync("i1").text == "hello"
