"""Store-layer protocol definitions for contextweaver.

Backend-agnostic interfaces for the four optional stores used by the Context
Engine (event log, artifacts, episodic memory, fact memory). Concrete
implementations live alongside this module under :mod:`contextweaver.store`.

Re-exported from :mod:`contextweaver.protocols` for backward compatibility.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from contextweaver.store.episodic import Episode
    from contextweaver.store.facts import Fact
    from contextweaver.types import ArtifactRef, ContextItem, ItemKind


# ---------------------------------------------------------------------------
# EventLog
# ---------------------------------------------------------------------------


@runtime_checkable
class EventLog(Protocol):
    """Read/write interface to the ordered event log.

    The event log is the ordered sequence of :class:`~contextweaver.types.ContextItem`
    objects that makes up a conversation / agent session.
    """

    def append(self, item: ContextItem) -> None:
        """Append *item* to the log.

        Raises:
            DuplicateItemError: If an item with the same ``id`` already exists.
        """
        ...

    def get(self, item_id: str) -> ContextItem:
        """Return the item with *item_id*.

        Raises:
            ItemNotFoundError: If no item with *item_id* exists.
        """
        ...

    def all(self) -> list[ContextItem]:
        """Return all items in insertion order."""
        ...

    def filter_by_kind(self, *kinds: ItemKind) -> list[ContextItem]:
        """Return all items whose ``kind`` is in *kinds*."""
        ...

    def tail(self, n: int) -> list[ContextItem]:
        """Return the last *n* items."""
        ...

    def children(self, parent_id: str) -> list[ContextItem]:
        """Return all items whose ``parent_id`` equals *parent_id*."""
        ...

    def parent(self, item_id: str) -> ContextItem | None:
        """Return the parent of *item_id*, or ``None``."""
        ...

    def query(
        self,
        kinds: list[ItemKind] | None = None,
        since: int | None = None,
        limit: int | None = None,
    ) -> list[ContextItem]:
        """Flexible query over the event log."""
        ...

    def count(self) -> int:
        """Return the number of items in the log."""
        ...

    def __len__(self) -> int: ...


# ---------------------------------------------------------------------------
# ArtifactStore
# ---------------------------------------------------------------------------


@runtime_checkable
class ArtifactStore(Protocol):
    """Read/write interface to the out-of-band artifact store.

    Raw tool outputs are stored here; the LLM context pipeline receives only
    :class:`~contextweaver.types.ArtifactRef` handles and summaries.
    """

    def put(
        self,
        handle: str,
        content: bytes,
        media_type: str = "application/octet-stream",
        label: str = "",
    ) -> ArtifactRef:
        """Store *content* and return an :class:`~contextweaver.types.ArtifactRef`."""
        ...

    def get(self, handle: str) -> bytes:
        """Retrieve the raw bytes for *handle*.

        Raises:
            ArtifactNotFoundError: If *handle* is not in the store.
        """
        ...

    def ref(self, handle: str) -> ArtifactRef:
        """Return the :class:`~contextweaver.types.ArtifactRef` metadata for *handle*."""
        ...

    def list_refs(self) -> list[ArtifactRef]:
        """Return all stored :class:`~contextweaver.types.ArtifactRef` objects."""
        ...

    def delete(self, handle: str) -> None:
        """Remove the artifact identified by *handle*."""
        ...

    def exists(self, handle: str) -> bool:
        """Return ``True`` if *handle* is in the store."""
        ...

    def metadata(self, handle: str) -> ArtifactRef:
        """Return the :class:`~contextweaver.types.ArtifactRef` for *handle*."""
        ...

    def drilldown(self, handle: str, selector: dict[str, Any]) -> str:
        """Return a subset of the artifact's content according to *selector*."""
        ...


# ---------------------------------------------------------------------------
# EpisodicStore
# ---------------------------------------------------------------------------


@runtime_checkable
class EpisodicStore(Protocol):
    """Read/write interface to the episodic memory store.

    The episodic store holds compressed summaries of past agent episodes
    (conversations / task runs).
    """

    def add(self, episode: Episode) -> None:
        """Append *episode* to the store."""
        ...

    def get(self, episode_id: str) -> Episode | None:
        """Return the episode with *episode_id*, or ``None`` if not found."""
        ...

    def search(self, query: str, top_k: int = 5) -> list[Episode]:
        """Return the *top_k* most relevant episodes for *query*."""
        ...

    def all(self) -> list[Episode]:
        """Return all episodes in insertion order."""
        ...

    def latest(self, n: int = 3) -> list[tuple[str, str, dict[str, Any]]]:
        """Return the *n* most recently added episodes.

        Returns:
            A list of ``(episode_id, summary, metadata)`` tuples, most-recent first.
        """
        ...

    def delete(self, episode_id: str) -> None:
        """Remove the episode with *episode_id*.

        Raises:
            ItemNotFoundError: If no episode with *episode_id* exists.
        """
        ...


# ---------------------------------------------------------------------------
# FactStore
# ---------------------------------------------------------------------------


@runtime_checkable
class FactStore(Protocol):
    """Read/write interface to the fact memory store.

    The fact store holds short, structured memory facts (key/value assertions)
    that can be injected into context as ``memory_fact`` items.
    """

    def put(self, fact: Fact) -> None:
        """Insert or replace the fact identified by ``fact.fact_id``."""
        ...

    def get(self, fact_id: str) -> Fact:
        """Return the fact with *fact_id*.

        Raises:
            ItemNotFoundError: If no fact with *fact_id* exists.
        """
        ...

    def get_by_key(self, key: str) -> list[Fact]:
        """Return all facts whose ``key`` matches *key*."""
        ...

    def list_keys(self, prefix: str = "") -> list[str]:
        """Return all distinct fact keys, optionally filtered by *prefix*."""
        ...

    def delete(self, fact_id: str) -> None:
        """Remove the fact identified by *fact_id*.

        Raises:
            ItemNotFoundError: If no fact with *fact_id* exists.
        """
        ...

    def all(self) -> list[Fact]:
        """Return all facts sorted by ``fact_id``."""
        ...
