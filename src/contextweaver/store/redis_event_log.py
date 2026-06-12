"""Redis-backed event log for contextweaver (issue #426).

A persistent :class:`~contextweaver.store.protocols.EventLog` for multi-process
/ long-lived gateways.  Items are stored in a Redis hash keyed by ``item.id``;
insertion order is preserved in a parallel list, so the append-only ordering
invariant holds across processes and restarts.

``redis`` is imported lazily (install ``pip install 'contextweaver[redis]'``).
Conformance-tested against ``fakeredis`` in CI; works unchanged against a real
server.  Pass either a ``redis.Redis`` client or a ``url``.
"""

from __future__ import annotations

import json
import logging
from types import TracebackType
from typing import TYPE_CHECKING

from contextweaver.exceptions import DuplicateItemError, ItemNotFoundError
from contextweaver.store.redis_artifacts import _require_redis
from contextweaver.types import ContextItem, ItemKind

if TYPE_CHECKING:
    import redis

logger = logging.getLogger("contextweaver.store")


class RedisEventLog:
    """Redis implementation of the :class:`EventLog` protocol.

    Args:
        client: A pre-configured ``redis.Redis`` client.  Mutually exclusive
            with *url*; one is required.
        url: A Redis connection URL used to build a client when *client* is not
            given.
        namespace: Key prefix isolating this log's keys (default ``"cw"``).

    Keys: ``{namespace}:events:items`` (hash ``id`` -> JSON
    :class:`~contextweaver.types.ContextItem`) and ``{namespace}:events:order``
    (list of ids in insertion order).

    Performance: the non-indexed reads (:meth:`filter_by_kind`, :meth:`children`,
    :meth:`parent`, :meth:`tail`, :meth:`query`) fetch and JSON-decode the full
    log via :meth:`all` on each call, mirroring the in-memory backend's
    semantics.  This is O(n) per call and fine for typical gateway logs; a
    server-side index is a future optimisation if logs grow large.
    """

    def __init__(
        self,
        client: redis.Redis | None = None,
        *,
        url: str | None = None,
        namespace: str = "cw",
    ) -> None:
        if client is None:
            from contextweaver.exceptions import ConfigError

            if url is None:
                raise ConfigError("RedisEventLog requires either a client or a url")
            client = _require_redis().Redis.from_url(url)
        self._client = client
        self._items_key = f"{namespace}:events:items"
        self._order_key = f"{namespace}:events:order"

    # ------------------------------------------------------------------
    # EventLog protocol
    # ------------------------------------------------------------------

    def append(self, item: ContextItem) -> None:
        """Append *item* to the log.

        Raises:
            DuplicateItemError: If an item with the same ``id`` already exists.
        """
        payload = json.dumps(item.to_dict(), sort_keys=True)
        # HSETNX is atomic: it sets the field only if absent, so a duplicate id
        # is rejected without a read-then-write race.
        if not self._client.hsetnx(self._items_key, item.id, payload):
            raise DuplicateItemError(f"Duplicate item id: {item.id!r}")
        self._client.rpush(self._order_key, item.id)
        logger.debug("redis_event_log.append: id=%s, kind=%s", item.id, item.kind.value)

    def get(self, item_id: str) -> ContextItem:
        """Return the item with *item_id*.

        Raises:
            ItemNotFoundError: If no item with *item_id* exists.
        """
        raw = self._client.hget(self._items_key, item_id)
        if raw is None:
            raise ItemNotFoundError(f"Item not found: {item_id!r}")
        return ContextItem.from_dict(json.loads(raw))

    def all(self) -> list[ContextItem]:
        """Return all items in insertion order."""
        ids = self._client.lrange(self._order_key, 0, -1)
        if not ids:
            return []
        payloads = self._client.hmget(self._items_key, ids)
        return [ContextItem.from_dict(json.loads(p)) for p in payloads if p is not None]

    def filter_by_kind(self, *kinds: ItemKind) -> list[ContextItem]:
        """Return all items whose ``kind`` is in *kinds*."""
        if not kinds:
            return []
        wanted = set(kinds)
        return [item for item in self.all() if item.kind in wanted]

    def tail(self, n: int) -> list[ContextItem]:
        """Return the last *n* items (``n <= 0`` returns an empty list)."""
        if n <= 0:
            return []
        return self.all()[-n:]

    def children(self, parent_id: str) -> list[ContextItem]:
        """Return all items whose ``parent_id`` equals *parent_id*."""
        return [item for item in self.all() if item.parent_id == parent_id]

    def parent(self, item_id: str) -> ContextItem | None:
        """Return the parent of *item_id*, or ``None``.

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

        Matches the in-memory backend exactly: ``since`` slices the full
        insertion-ordered log *before* the ``kinds`` filter, then ``limit``.
        """
        if kinds is not None and not kinds:
            return []
        items = self.all()
        if since is not None:
            items = items[since:]
        if kinds is not None:
            wanted = set(kinds)
            items = [i for i in items if i.kind in wanted]
        if limit is not None:
            items = items[:limit]
        return items

    def count(self) -> int:
        """Return the number of items in the log."""
        return int(self._client.llen(self._order_key))

    def __len__(self) -> int:
        return self.count()

    def close(self) -> None:
        """No-op: the Redis client lifecycle is owned by the caller."""

    def __enter__(self) -> RedisEventLog:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
