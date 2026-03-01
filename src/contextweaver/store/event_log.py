"""In-memory event log for contextweaver.

The event log is the ordered sequence of ContextItem objects that makes up
a conversation / agent session.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from contextweaver.exceptions import ItemNotFoundError
from contextweaver.types import ContextItem, ItemKind


@runtime_checkable
class EventLog(Protocol):
    """Append-only log of all ingested ContextItems."""

    async def append(self, item: ContextItem) -> None: ...
    async def query(
        self,
        kinds: list[ItemKind] | None = None,
        since: float | None = None,
        limit: int | None = None,
    ) -> list[ContextItem]: ...
    async def get(self, item_id: str) -> ContextItem: ...
    async def children(self, parent_id: str) -> list[ContextItem]: ...
    async def parent(self, item_id: str) -> ContextItem | None: ...
    async def count(self) -> int: ...


class InMemoryEventLog:
    """Default EventLog implementation. Stores items in a list."""

    def __init__(self) -> None:
        self._items: list[ContextItem] = []
        self._index: dict[str, int] = {}

    async def append(self, item: ContextItem) -> None:
        """Append *item* to the log. Raises ValueError on duplicate id."""
        if item.id in self._index:
            raise ValueError(f"Duplicate item id: {item.id!r}")
        self._index[item.id] = len(self._items)
        self._items.append(item)

    async def query(
        self,
        kinds: list[ItemKind] | None = None,
        since: float | None = None,
        limit: int | None = None,
    ) -> list[ContextItem]:
        """Return items matching the filters."""
        result = list(self._items)
        if kinds is not None:
            kind_set = set(kinds)
            result = [i for i in result if i.kind in kind_set]
        if since is not None:
            result = [i for i in result if i.metadata.get("timestamp", 0.0) >= since]
        if limit is not None:
            result = result[:limit]
        return result

    async def get(self, item_id: str) -> ContextItem:
        """Return the item with *item_id*. Raises ItemNotFoundError."""
        if item_id not in self._index:
            raise ItemNotFoundError(f"Item not found: {item_id!r}")
        return self._items[self._index[item_id]]

    async def children(self, parent_id: str) -> list[ContextItem]:
        """Return all items whose parent_id matches."""
        return [i for i in self._items if i.parent_id == parent_id]

    async def parent(self, item_id: str) -> ContextItem | None:
        """Return the parent of *item_id*, or None."""
        if item_id not in self._index:
            raise ItemNotFoundError(f"Item not found: {item_id!r}")
        item = self._items[self._index[item_id]]
        if item.parent_id is None:
            return None
        if item.parent_id not in self._index:
            return None
        return self._items[self._index[item.parent_id]]

    async def count(self) -> int:
        """Return number of items."""
        return len(self._items)

    def all_sync(self) -> list[ContextItem]:
        """Synchronous access to all items (for pipeline stages)."""
        return list(self._items)

    def get_sync(self, item_id: str) -> ContextItem:
        """Synchronous get."""
        if item_id not in self._index:
            raise ItemNotFoundError(f"Item not found: {item_id!r}")
        return self._items[self._index[item_id]]

    def __len__(self) -> int:
        return len(self._items)

    def to_dict(self) -> dict[str, Any]:
        """Serialise the event log to a JSON-compatible dict."""
        return {"items": [item.to_dict() for item in self._items]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InMemoryEventLog:
        """Deserialise from a dict. Uses sync internals for bootstrapping."""
        log = cls()
        for raw in data.get("items", []):
            item = ContextItem.from_dict(raw)
            log._index[item.id] = len(log._items)
            log._items.append(item)
        return log
