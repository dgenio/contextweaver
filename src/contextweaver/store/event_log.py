"""In-memory event log for contextweaver.

The event log is the ordered sequence of :class:`~contextweaver.types.ContextItem`
objects that makes up a conversation / agent session.  The Context Engine reads
from this store when generating context candidates.
"""

from __future__ import annotations

import logging
from typing import Any

from contextweaver.exceptions import ItemNotFoundError
from contextweaver.types import ContextItem, ItemKind

logger = logging.getLogger("contextweaver.store")


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
        logger.debug("event_log.append: id=%s, kind=%s", item.id, item.kind.value)

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

    def children(self, parent_id: str) -> list[ContextItem]:
        """Return all items whose ``parent_id`` equals *parent_id*.

        Args:
            parent_id: The ID of the parent item.

        Returns:
            A list of child items in insertion order.
        """
        return [item for item in self._items if item.parent_id == parent_id]

    def parent(self, item_id: str) -> ContextItem | None:
        """Return the parent item of the item with *item_id*, or ``None``.

        Args:
            item_id: The item whose parent to look up.

        Returns:
            The parent :class:`~contextweaver.types.ContextItem`, or ``None`` if the
            item has no parent or the parent is not in the log.

        Raises:
            ItemNotFoundError: If no item with *item_id* exists.
        """
        item = self.get(item_id)
        if item.parent_id is None:
            return None
        try:
            return self.get(item.parent_id)
        except ItemNotFoundError:
            return None

    def query(
        self,
        kinds: list[ItemKind] | None = None,
        since: int | None = None,
        limit: int | None = None,
    ) -> list[ContextItem]:
        """Flexible query over the event log.

        Args:
            kinds: If given, only include items whose kind is in this list.
            since: If given, only include items at or after this positional index.
            limit: If given, return at most this many items.

        Returns:
            A list of matching items in insertion order.
        """
        items: list[ContextItem] = list(self._items)
        if since is not None:
            items = items[since:]
        if kinds is not None:
            kind_set = set(kinds)
            items = [item for item in items if item.kind in kind_set]
        if limit is not None:
            items = items[:limit]
        return items

    def count(self) -> int:
        """Return the number of items in the log."""
        return len(self._items)

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
