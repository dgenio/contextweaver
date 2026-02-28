"""In-memory event log for contextweaver.

The event log is the ordered sequence of :class:`~contextweaver.types.ContextItem`
objects that makes up a conversation / agent session.  The Context Engine reads
from this store when generating context candidates.
"""

from __future__ import annotations

from typing import Any

from contextweaver.exceptions import ItemNotFoundError
from contextweaver.types import ContextItem, ItemKind


class InMemoryEventLog:
    """Append-only, in-memory event log.

    Items are stored in insertion order.  All look-ups are O(n) — for
    production use, replace with an indexed persistent store.
    """

    def __init__(self) -> None:
        self._items: list[ContextItem] = []
        self._index: dict[str, int] = {}

    def append(self, item: ContextItem) -> None:
        """Append *item* to the log.

        Args:
            item: The :class:`~contextweaver.types.ContextItem` to append.

        Raises:
            ValueError: If an item with the same ``id`` already exists.
        """
        if item.id in self._index:
            raise ValueError(f"Duplicate item id: {item.id!r}")
        self._index[item.id] = len(self._items)
        self._items.append(item)

    def get(self, item_id: str) -> ContextItem:
        """Return the item with *item_id*.

        Args:
            item_id: The unique identifier of the item.

        Returns:
            The matching :class:`~contextweaver.types.ContextItem`.

        Raises:
            ItemNotFoundError: If no item with *item_id* exists.
        """
        if item_id not in self._index:
            raise ItemNotFoundError(f"Item not found: {item_id!r}")
        return self._items[self._index[item_id]]

    def all(self) -> list[ContextItem]:
        """Return all items in insertion order.

        Returns:
            A shallow copy of the internal item list.
        """
        return list(self._items)

    def filter_by_kind(self, *kinds: ItemKind) -> list[ContextItem]:
        """Return all items whose ``kind`` is in *kinds*.

        Args:
            *kinds: One or more :class:`~contextweaver.types.ItemKind` values to include.

        Returns:
            A list of matching items in insertion order.
        """
        kind_set = set(kinds)
        return [item for item in self._items if item.kind in kind_set]

    def tail(self, n: int) -> list[ContextItem]:
        """Return the last *n* items.

        Args:
            n: Number of most-recent items to return.

        Returns:
            A list of up to *n* items, most-recent last.
        """
        return self._items[-n:] if n > 0 else []

    def __len__(self) -> int:
        return len(self._items)

    def to_dict(self) -> dict[str, Any]:
        """Serialise the event log to a JSON-compatible dict."""
        return {"items": [item.to_dict() for item in self._items]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InMemoryEventLog:
        """Deserialise from a JSON-compatible dict produced by :meth:`to_dict`."""
        log = cls()
        for raw in data.get("items", []):
            log.append(ContextItem.from_dict(raw))
        return log
