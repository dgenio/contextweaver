"""LangMem / LangGraph ``EpisodicStore`` / ``FactStore`` backend (issue #195).

[LangMem](https://langchain-ai.github.io/langmem/) builds its long-term memory
on top of a LangGraph **`BaseStore`** — the namespaced key/value store
(`langgraph.store.base.BaseStore`) shared across threads in a LangGraph
deployment.  This adapter wraps any such store so contextweaver's optional
long-lived stores can read and write the memory you already persist in a
LangGraph app (an `InMemoryStore`, a `PostgresStore`, or LangMem's own
store-backed managers), without changing core pipeline code.

The two classes here implement the existing
:class:`~contextweaver.store.protocols.EpisodicStore` and
:class:`~contextweaver.store.protocols.FactStore` Protocols verbatim — the
Protocols are not widened.

This module requires the ``[langmem]`` optional extra::

    pip install 'contextweaver[langmem]'

Without it (specifically, without ``langgraph`` installed), importing this
module raises :class:`ImportError` with the exact install hint above.  The
rest of contextweaver works unchanged.

How items are persisted
-----------------------

Unlike a graph-extraction backend, a `BaseStore` is a faithful namespaced
KV store, so the mapping is direct and lossless:

* Each adapter is scoped to a ``namespace`` tuple (e.g. ``("agent", "bot-1")``).
  Episodes live under ``(*namespace, "episodes")`` and facts under
  ``(*namespace, "facts")`` so the two never collide.
* The canonical ``episode_id`` / ``fact_id`` is the store **key**; the value
  is the dataclass' :meth:`to_dict` payload.  ``get`` / ``delete`` therefore
  hit the store by key directly (no scan), and ``put`` / ``add`` are native
  upserts.
* :meth:`EpisodicStore.search` delegates to :meth:`BaseStore.search`, passing
  the query through.  When the wrapped store has a vector index configured
  (LangMem's typical setup) this yields semantic recall; without an index the
  store returns the namespace's items and contextweaver still budgets them
  downstream.

Scan bound
----------

Methods that must enumerate a namespace (:meth:`EpisodicStore.all` /
:meth:`latest`, :meth:`FactStore.get_by_key` / :meth:`list_keys` /
:meth:`all`) request up to ``scan_limit`` items.  If the namespace holds at
least that many, the enumeration is no longer guaranteed complete and the
method raises :class:`NotImplementedError` rather than silently truncating —
narrow the ``namespace`` or use a dedicated store.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from contextweaver.exceptions import ContextWeaverError, ItemNotFoundError
from contextweaver.store.episodic import Episode
from contextweaver.store.facts import Fact

if TYPE_CHECKING:
    from langgraph.store.base import BaseStore, SearchItem


try:
    from langgraph.store.base import BaseStore as _BaseStore  # noqa: F401
except (
    ImportError
) as _langmem_import_err:  # pragma: no cover - exercised only when extra is missing
    raise ImportError(
        "LangMem external-memory backend requires the [langmem] extra. "
        "Install with: pip install 'contextweaver[langmem]'"
    ) from _langmem_import_err


_EPISODES = "episodes"
_FACTS = "facts"
_DEFAULT_SCAN_LIMIT = 1000


class LangMemBackendError(ContextWeaverError):
    """Raised when the LangMem / BaseStore backend cannot honour an operation."""


def _value_of(item: object) -> dict[str, Any]:
    """Return the ``value`` dict of a BaseStore ``Item`` / ``SearchItem``."""
    value = getattr(item, "value", None)
    return dict(value) if isinstance(value, dict) else {}


def _created_at_of(item: object) -> object:
    """Return the ``created_at`` of a store item for ordering (or ``None``)."""
    return getattr(item, "created_at", None)


class LangMemEpisodicStore:
    """:class:`~contextweaver.store.protocols.EpisodicStore` backed by a LangGraph store.

    Args:
        store: A configured :class:`langgraph.store.base.BaseStore` instance.
            Bring your own — the adapter does not configure the index /
            backend.
        namespace: Namespace prefix tuple scoping every operation.  Episodes
            are stored under ``(*namespace, "episodes")``.  Use a stable value
            per agent / per tenant.  Defaults to ``("contextweaver",)``.
        scan_limit: Upper bound on items enumerated per :meth:`all` /
            :meth:`latest` call.  Defaults to ``1000``.
    """

    def __init__(
        self,
        store: BaseStore,
        *,
        namespace: tuple[str, ...] = ("contextweaver",),
        scan_limit: int = _DEFAULT_SCAN_LIMIT,
    ) -> None:
        if not namespace:
            raise LangMemBackendError("LangMemEpisodicStore requires a non-empty namespace.")
        self._store = store
        self._ns = (*namespace, _EPISODES)
        self._scan_limit = scan_limit

    def add(self, episode: Episode) -> None:
        """Persist *episode* (native upsert keyed by ``episode_id``)."""
        self._store.put(self._ns, episode.episode_id, episode.to_dict())

    def get(self, episode_id: str) -> Episode | None:
        """Return the episode with ``episode_id`` or ``None`` (direct key lookup)."""
        item = self._store.get(self._ns, episode_id)
        if item is None:
            return None
        return Episode.from_dict(_value_of(item))

    def search(self, query: str, top_k: int = 5) -> list[Episode]:
        """Return up to ``top_k`` episodes most relevant to *query*.

        Delegates to :meth:`BaseStore.search`; semantic when the wrapped
        store has an index configured.
        """
        results = self._store.search(self._ns, query=query, limit=top_k)
        return [Episode.from_dict(_value_of(r)) for r in results]

    def _scan(self) -> list[SearchItem]:
        items = list(self._store.search(self._ns, limit=self._scan_limit))
        if len(items) >= self._scan_limit:
            raise NotImplementedError(
                f"LangMemEpisodicStore: namespace {self._ns!r} holds at least "
                f"{self._scan_limit} episodes; enumeration is no longer complete. "
                "Narrow the namespace or use a dedicated EpisodicStore backend."
            )
        return items

    def _ordered(self) -> list[SearchItem]:
        """Return scanned items ordered by ``created_at`` then key (insertion-like)."""
        return sorted(
            self._scan(), key=lambda it: (str(_created_at_of(it)), getattr(it, "key", ""))
        )

    def all(self) -> list[Episode]:
        """Return every episode in the namespace, insertion-ordered."""
        return [Episode.from_dict(_value_of(it)) for it in self._ordered()]

    def latest(self, n: int = 3) -> list[tuple[str, str, dict[str, Any]]]:
        """Return the *n* most recently added episodes, most-recent first."""
        if n <= 0:
            return []
        recent = list(reversed(self._ordered()))[:n]
        out: list[tuple[str, str, dict[str, Any]]] = []
        for it in recent:
            ep = Episode.from_dict(_value_of(it))
            out.append((ep.episode_id, ep.summary, dict(ep.metadata)))
        return out

    def delete(self, episode_id: str) -> None:
        """Remove the episode with ``episode_id``.

        Raises:
            ItemNotFoundError: When no such episode exists in the namespace.
        """
        if self._store.get(self._ns, episode_id) is None:
            raise ItemNotFoundError(f"Episode not found: {episode_id!r}")
        self._store.delete(self._ns, episode_id)


class LangMemFactStore:
    """:class:`~contextweaver.store.protocols.FactStore` backed by a LangGraph store.

    See the :mod:`module docstring <contextweaver.extras.memory.langmem>` for
    the namespacing and scan-limit semantics that apply equally to facts.

    Args:
        store: A configured :class:`langgraph.store.base.BaseStore` instance.
        namespace: Namespace prefix tuple; facts live under
            ``(*namespace, "facts")``.  Defaults to ``("contextweaver",)``.
        scan_limit: Upper bound on items enumerated per
            :meth:`get_by_key` / :meth:`list_keys` / :meth:`all` call.
    """

    def __init__(
        self,
        store: BaseStore,
        *,
        namespace: tuple[str, ...] = ("contextweaver",),
        scan_limit: int = _DEFAULT_SCAN_LIMIT,
    ) -> None:
        if not namespace:
            raise LangMemBackendError("LangMemFactStore requires a non-empty namespace.")
        self._store = store
        self._ns = (*namespace, _FACTS)
        self._scan_limit = scan_limit

    def put(self, fact: Fact) -> None:
        """Insert or replace the fact identified by ``fact.fact_id`` (native upsert)."""
        self._store.put(self._ns, fact.fact_id, fact.to_dict())

    def get(self, fact_id: str) -> Fact:
        """Return the fact with ``fact_id`` (direct key lookup).

        Raises:
            ItemNotFoundError: When no such fact exists in the namespace.
        """
        item = self._store.get(self._ns, fact_id)
        if item is None:
            raise ItemNotFoundError(f"Fact not found: {fact_id!r}")
        return Fact.from_dict(_value_of(item))

    def _scan(self) -> list[SearchItem]:
        items = list(self._store.search(self._ns, limit=self._scan_limit))
        if len(items) >= self._scan_limit:
            raise NotImplementedError(
                f"LangMemFactStore: namespace {self._ns!r} holds at least "
                f"{self._scan_limit} facts; enumeration is no longer complete. "
                "Narrow the namespace or use a dedicated FactStore backend."
            )
        return items

    def get_by_key(self, key: str) -> list[Fact]:
        """Return every fact whose ``key`` matches *key*, sorted by ``fact_id``."""
        out = [f for f in (Fact.from_dict(_value_of(it)) for it in self._scan()) if f.key == key]
        out.sort(key=lambda f: f.fact_id)
        return out

    def list_keys(self, prefix: str = "") -> list[str]:
        """Return every distinct fact key in the namespace, optionally prefix-filtered."""
        keys = {
            f.key
            for f in (Fact.from_dict(_value_of(it)) for it in self._scan())
            if f.key.startswith(prefix)
        }
        return sorted(keys)

    def delete(self, fact_id: str) -> None:
        """Remove the fact identified by ``fact_id``.

        Raises:
            ItemNotFoundError: When no such fact exists in the namespace.
        """
        if self._store.get(self._ns, fact_id) is None:
            raise ItemNotFoundError(f"Fact not found: {fact_id!r}")
        self._store.delete(self._ns, fact_id)

    def all(self) -> list[Fact]:
        """Return every fact in the namespace, sorted by ``fact_id``."""
        out = [Fact.from_dict(_value_of(it)) for it in self._scan()]
        out.sort(key=lambda f: f.fact_id)
        return out
