"""In-memory fact store for contextweaver.

The fact store holds short, structured memory facts (key/value assertions)
that can be injected into the context as ``memory_fact`` items.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from contextweaver.exceptions import ItemNotFoundError


@dataclass
class Fact:
    """A single memory fact."""

    fact_id: str
    key: str
    value: str
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "fact_id": self.fact_id,
            "key": self.key,
            "value": self.value,
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Fact:
        """Deserialise from a JSON-compatible dict."""
        return cls(
            fact_id=data["fact_id"],
            key=data["key"],
            value=data["value"],
            tags=list(data.get("tags", [])),
            metadata=dict(data.get("metadata", {})),
        )


class InMemoryFactStore:
    """Simple in-memory key/value fact store.

    Facts are uniquely identified by ``fact_id``.  Duplicate *keys* are
    allowed — callers can model key history by appending multiple facts with
    the same ``key``.
    """

    def __init__(self) -> None:
        self._facts: dict[str, Fact] = {}

    def put(self, fact: Fact) -> None:
        """Insert or replace the fact identified by ``fact.fact_id``.

        Args:
            fact: The :class:`Fact` to store.
        """
        self._facts[fact.fact_id] = fact

    def get(self, fact_id: str) -> Fact:
        """Return the fact with *fact_id*.

        Args:
            fact_id: The unique identifier of the fact.

        Returns:
            The matching :class:`Fact`.

        Raises:
            ItemNotFoundError: If no fact with *fact_id* exists.
        """
        if fact_id not in self._facts:
            raise ItemNotFoundError(f"Fact not found: {fact_id!r}")
        return self._facts[fact_id]

    def get_by_key(self, key: str) -> list[Fact]:
        """Return all facts whose ``key`` matches *key*.

        Args:
            key: The key string to filter on.

        Returns:
            A list of matching facts, sorted by ``fact_id`` for determinism.
        """
        return sorted(
            (f for f in self._facts.values() if f.key == key),
            key=lambda f: f.fact_id,
        )

    def delete(self, fact_id: str) -> None:
        """Remove the fact identified by *fact_id*.

        Args:
            fact_id: The unique identifier of the fact to delete.

        Raises:
            ItemNotFoundError: If no fact with *fact_id* exists.
        """
        if fact_id not in self._facts:
            raise ItemNotFoundError(f"Fact not found: {fact_id!r}")
        del self._facts[fact_id]

    def list_keys(self, prefix: str = "") -> list[str]:
        """Return distinct keys, optionally filtered by *prefix*.

        Args:
            prefix: Only include keys starting with this string.
                An empty prefix returns all keys.

        Returns:
            A sorted list of unique key strings.
        """
        keys: set[str] = set()
        for fact in self._facts.values():
            if fact.key.startswith(prefix):
                keys.add(fact.key)
        return sorted(keys)

    def get_all(self) -> list[Fact]:
        """Return all facts sorted by ``fact_id``.

        Alias for :meth:`all`.

        Returns:
            A list of all stored :class:`Fact` objects.
        """
        return self.all()

    def all(self) -> list[Fact]:
        """Return all facts sorted by ``fact_id``.

        Returns:
            A list of all stored :class:`Fact` objects.
        """
        return [self._facts[k] for k in sorted(self._facts)]

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {"facts": [f.to_dict() for f in self.all()]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InMemoryFactStore:
        """Deserialise from a JSON-compatible dict produced by :meth:`to_dict`."""
        store = cls()
        for raw in data.get("facts", []):
            store.put(Fact.from_dict(raw))
        return store
