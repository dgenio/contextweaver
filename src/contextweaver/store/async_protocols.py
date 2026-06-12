"""Async variants of the store-layer protocols (issue #495).

The synchronous protocols in :mod:`contextweaver.store.protocols` are the
canonical store surface and remain unchanged.  These async counterparts mirror
them method-for-method with ``async def`` signatures so that **network-backed**
backends — the planned Redis/S3 stores (#426), the external memory services —
can be integrated without blocking the async-first context pipeline on I/O.

Two adapter families bridge the two flavours (see
:mod:`contextweaver.store.async_bridge`):

- :func:`~contextweaver.store.async_bridge.to_async` wraps any *sync* store as
  the matching async protocol via :func:`asyncio.to_thread`, so every existing
  backend works under the async interface immediately.
- :func:`~contextweaver.store.async_bridge.to_sync` wraps any *async* store as
  the matching sync protocol via a private loop thread, so the existing
  synchronous pipeline (and :class:`~contextweaver.context.manager.ContextManager`)
  can consume an async backend without being rewritten.

The protocols are :func:`~typing.runtime_checkable` so callers (and the
``ContextManager``) can detect the flavour of a store with ``isinstance``.
``routing/`` stays sync-only per the architecture invariants — these protocols
are consumed only by the async ``context/`` surface.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from contextweaver.store.episodic import Episode
    from contextweaver.store.facts import Fact
    from contextweaver.types import ArtifactRef, ContextItem, ItemKind


# ---------------------------------------------------------------------------
# AsyncEventLog
# ---------------------------------------------------------------------------


@runtime_checkable
class AsyncEventLog(Protocol):
    """Async counterpart of :class:`~contextweaver.store.protocols.EventLog`."""

    async def append(self, item: ContextItem) -> None:
        """Append *item* to the log.

        Raises:
            DuplicateItemError: If an item with the same ``id`` already exists.
        """
        ...

    async def get(self, item_id: str) -> ContextItem:
        """Return the item with *item_id*.

        Raises:
            ItemNotFoundError: If no item with *item_id* exists.
        """
        ...

    async def all(self) -> list[ContextItem]:
        """Return all items in insertion order."""
        ...

    async def filter_by_kind(self, *kinds: ItemKind) -> list[ContextItem]:
        """Return all items whose ``kind`` is in *kinds*."""
        ...

    async def tail(self, n: int) -> list[ContextItem]:
        """Return the last *n* items."""
        ...

    async def children(self, parent_id: str) -> list[ContextItem]:
        """Return all items whose ``parent_id`` equals *parent_id*."""
        ...

    async def parent(self, item_id: str) -> ContextItem | None:
        """Return the parent of *item_id*, or ``None``."""
        ...

    async def query(
        self,
        kinds: list[ItemKind] | None = None,
        since: int | None = None,
        limit: int | None = None,
    ) -> list[ContextItem]:
        """Flexible query over the event log."""
        ...

    async def count(self) -> int:
        """Return the number of items in the log."""
        ...

    async def close(self) -> None:
        """Release any backend resources held by the log.

        Calling :meth:`close` more than once must be safe.
        """
        ...


# ---------------------------------------------------------------------------
# AsyncArtifactStore
# ---------------------------------------------------------------------------


@runtime_checkable
class AsyncArtifactStore(Protocol):
    """Async counterpart of :class:`~contextweaver.store.protocols.ArtifactStore`."""

    async def put(
        self,
        handle: str,
        content: bytes,
        media_type: str = "application/octet-stream",
        label: str = "",
    ) -> ArtifactRef:
        """Store *content* and return its :class:`~contextweaver.types.ArtifactRef`.

        The returned ref MUST carry a populated ``content_hash`` (sha256 hex of
        *content*); the firewall's content-addressed idempotency short-circuit
        (#190) depends on it, exactly as for the sync protocol.
        """
        ...

    async def get(self, handle: str) -> bytes:
        """Retrieve the raw bytes for *handle*.

        Raises:
            ArtifactNotFoundError: If *handle* is not in the store.
        """
        ...

    async def ref(self, handle: str) -> ArtifactRef:
        """Return the :class:`~contextweaver.types.ArtifactRef` metadata for *handle*."""
        ...

    async def list_refs(self) -> list[ArtifactRef]:
        """Return all stored :class:`~contextweaver.types.ArtifactRef` objects."""
        ...

    async def delete(self, handle: str) -> None:
        """Remove the artifact identified by *handle*."""
        ...

    async def exists(self, handle: str) -> bool:
        """Return ``True`` if *handle* is in the store."""
        ...

    async def metadata(self, handle: str) -> ArtifactRef:
        """Return the :class:`~contextweaver.types.ArtifactRef` for *handle*."""
        ...

    async def drilldown(self, handle: str, selector: dict[str, Any]) -> str:
        """Return a subset of the artifact's content according to *selector*."""
        ...


# ---------------------------------------------------------------------------
# AsyncEpisodicStore
# ---------------------------------------------------------------------------


@runtime_checkable
class AsyncEpisodicStore(Protocol):
    """Async counterpart of :class:`~contextweaver.store.protocols.EpisodicStore`."""

    async def add(self, episode: Episode) -> None:
        """Append *episode* to the store."""
        ...

    async def get(self, episode_id: str) -> Episode | None:
        """Return the episode with *episode_id*, or ``None`` if not found."""
        ...

    async def search(self, query: str, top_k: int = 5) -> list[Episode]:
        """Return the *top_k* most relevant episodes for *query*."""
        ...

    async def all(self) -> list[Episode]:
        """Return all episodes in insertion order."""
        ...

    async def latest(self, n: int = 3) -> list[tuple[str, str, dict[str, Any]]]:
        """Return the *n* most recently added episodes, most-recent first."""
        ...

    async def delete(self, episode_id: str) -> None:
        """Remove the episode with *episode_id*.

        Raises:
            ItemNotFoundError: If no episode with *episode_id* exists.
        """
        ...


# ---------------------------------------------------------------------------
# AsyncFactStore
# ---------------------------------------------------------------------------


@runtime_checkable
class AsyncFactStore(Protocol):
    """Async counterpart of :class:`~contextweaver.store.protocols.FactStore`."""

    async def put(self, fact: Fact) -> None:
        """Insert or replace the fact identified by ``fact.fact_id`` (upsert)."""
        ...

    async def get(self, fact_id: str) -> Fact:
        """Return the fact with *fact_id*.

        Raises:
            ItemNotFoundError: If no fact with *fact_id* exists.
        """
        ...

    async def get_by_key(self, key: str) -> list[Fact]:
        """Return all facts whose ``key`` matches *key*."""
        ...

    async def list_keys(self, prefix: str = "") -> list[str]:
        """Return all distinct fact keys, optionally filtered by *prefix*."""
        ...

    async def delete(self, fact_id: str) -> None:
        """Remove the fact identified by *fact_id*.

        Raises:
            ItemNotFoundError: If no fact with *fact_id* exists.
        """
        ...

    async def all(self) -> list[Fact]:
        """Return all facts sorted by ``fact_id``."""
        ...
