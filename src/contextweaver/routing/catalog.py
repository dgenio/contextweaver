"""Tool catalog management for the contextweaver Routing Engine.

The :class:`Catalog` holds all registered :class:`~contextweaver.types.SelectableItem`
objects and provides lookup, filtering, and namespace-scoped views.
"""

from __future__ import annotations

from typing import Any

from contextweaver.exceptions import CatalogError, ItemNotFoundError
from contextweaver.types import SelectableItem


class Catalog:
    """Registry of :class:`~contextweaver.types.SelectableItem` objects.

    All item IDs must be unique within a catalog.  Namespace filtering and
    tag-based queries are supported.
    """

    def __init__(self) -> None:
        self._items: dict[str, SelectableItem] = {}

    def register(self, item: SelectableItem) -> None:
        """Add *item* to the catalog.

        Args:
            item: The item to register.

        Raises:
            CatalogError: If an item with the same ``id`` is already registered.
        """
        if item.id in self._items:
            raise CatalogError(f"Duplicate item id: {item.id!r}")
        self._items[item.id] = item

    def get(self, item_id: str) -> SelectableItem:
        """Return the item with *item_id*.

        Args:
            item_id: Unique identifier.

        Returns:
            The matching :class:`~contextweaver.types.SelectableItem`.

        Raises:
            ItemNotFoundError: If no item with *item_id* exists.
        """
        if item_id not in self._items:
            raise ItemNotFoundError(f"Item not found: {item_id!r}")
        return self._items[item_id]

    def all(self) -> list[SelectableItem]:
        """Return all items sorted by id.

        Returns:
            A list of all registered items.
        """
        return [self._items[k] for k in sorted(self._items)]

    def filter_by_namespace(self, namespace: str) -> list[SelectableItem]:
        """Return items whose ``namespace`` matches *namespace*.

        Args:
            namespace: Exact namespace string to filter on.

        Returns:
            A list of matching items sorted by id.
        """
        return sorted(
            (item for item in self._items.values() if item.namespace == namespace),
            key=lambda i: i.id,
        )

    def filter_by_tags(self, *tags: str) -> list[SelectableItem]:
        """Return items that have **all** of the specified *tags*.

        Args:
            *tags: Tag strings that must all be present on the item.

        Returns:
            A list of matching items sorted by id.
        """
        tag_set = set(tags)
        return sorted(
            (item for item in self._items.values() if tag_set.issubset(item.tags)),
            key=lambda i: i.id,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {"items": [item.to_dict() for item in self.all()]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Catalog:
        """Deserialise from a JSON-compatible dict produced by :meth:`to_dict`."""
        catalog = cls()
        for raw in data.get("items", []):
            catalog.register(SelectableItem.from_dict(raw))
        return catalog
