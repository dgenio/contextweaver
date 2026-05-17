"""Mem0 ``EpisodicStore`` / ``FactStore`` backend for contextweaver (issue #195).

Adapts the [Mem0](https://docs.mem0.ai/) Python client so an existing
mem0 deployment can back contextweaver's optional long-lived stores
without changing core pipeline code.  The two classes here implement
the existing :class:`~contextweaver.store.protocols.EpisodicStore` and
:class:`~contextweaver.store.protocols.FactStore` Protocols verbatim —
the Protocols are not widened.

This module requires the ``[mem0]`` optional extra::

    pip install 'contextweaver[mem0]'

Without that extra, importing this module raises :class:`ImportError`
with the exact install hint above.  The rest of contextweaver works
unchanged.

How items are persisted
-----------------------

Mem0's ``Memory.add(...)`` was designed for "extract memories from a
conversation" — it assigns its own UUID per memory and treats every
call as the user offering content for ingestion.  contextweaver, by
contrast, hands the store an :class:`~contextweaver.store.episodic.Episode`
or :class:`~contextweaver.store.facts.Fact` whose ``episode_id`` /
``fact_id`` is the canonical identifier.  The adapter bridges these
two worlds by:

1. Calling :meth:`mem0.Memory.add` with ``infer=False`` so mem0 does
   **not** run an LLM extraction pass — the raw text is stored as-is.
2. Stamping every memory with a contextweaver-namespaced metadata key
   (``cw_episode_id`` or ``cw_fact_id``) so subsequent ``get`` /
   ``delete`` calls can resolve the canonical ID back to mem0's
   generated UUID by scanning :meth:`mem0.Memory.get_all`.

Search delegates to :meth:`mem0.Memory.search`, which uses mem0's
configured vector + reranker stack.  This is the primary reason to
choose mem0 over the in-memory store — higher-quality semantic recall.

Out-of-scope (raise :class:`NotImplementedError`)
-------------------------------------------------

Mem0 has no first-class concept of a ``key`` separate from the
content itself, so :meth:`Mem0FactStore.get_by_key` and
:meth:`Mem0FactStore.list_keys` reconstruct the answer client-side by
scanning :meth:`mem0.Memory.get_all`.  When the configured ``user_id``
holds more than ``scan_limit`` memories this becomes lossy — the
adapter then raises :class:`NotImplementedError` rather than silently
truncating.  Callers should narrow scope with ``user_id`` /
``agent_id`` / ``run_id`` partitioning or use a dedicated
``FactStore`` backend.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from contextweaver.exceptions import ContextWeaverError, ItemNotFoundError
from contextweaver.store.episodic import Episode
from contextweaver.store.facts import Fact

if TYPE_CHECKING:
    from mem0 import Memory


try:
    # ``mem0ai`` exposes its public symbols under the ``mem0`` import name
    # (the wheel is published as ``mem0ai`` to avoid PyPI conflicts; the
    # in-Python import path is ``mem0``).  See https://docs.mem0.ai/.
    from mem0 import Memory as _Memory  # noqa: F401 - imported for side effect
except ImportError as _mem0_import_err:  # pragma: no cover - exercised only when extra is missing
    raise ImportError(
        "Mem0 external-memory backend requires the [mem0] extra. "
        "Install with: pip install 'contextweaver[mem0]'"
    ) from _mem0_import_err


_CW_EPISODE_KEY = "cw_episode_id"
_CW_FACT_KEY = "cw_fact_id"
_CW_FACT_KEY_FIELD = "cw_key"
_CW_TAGS_FIELD = "cw_tags"
_DEFAULT_SCAN_LIMIT = 1000


class Mem0BackendError(ContextWeaverError):
    """Raised when the Mem0 backend cannot honour a store operation."""


def _normalise_get_all_response(raw: object) -> list[dict[str, Any]]:
    """Coerce mem0's ``get_all`` / ``search`` response into a plain memory list.

    mem0 2.x returns ``{"results": [...]}``; older clients returned a
    bare list.  Some self-hosted configurations return ``None`` for
    empty stores.  This helper accepts all three shapes.
    """
    if raw is None:
        return []
    if isinstance(raw, dict):
        results = raw.get("results", [])
        if isinstance(results, list):
            return [r for r in results if isinstance(r, dict)]
        return []
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    return []


def _memory_id(record: dict[str, Any]) -> str | None:
    """Return the mem0-assigned ``id`` of a memory record, or ``None``."""
    value = record.get("id")
    return value if isinstance(value, str) and value else None


def _memory_text(record: dict[str, Any]) -> str:
    """Return the textual payload of a memory record.

    mem0 stores the canonical text under ``memory`` in 2.x; older
    clients used ``text``.  Fall back to an empty string when the
    record lacks both.
    """
    for field in ("memory", "text", "content"):
        value = record.get(field)
        if isinstance(value, str):
            return value
    return ""


def _record_metadata(record: dict[str, Any]) -> dict[str, Any]:
    """Return the metadata sub-dict of a memory record.

    mem0 stores user-supplied metadata under the ``metadata`` key.
    """
    meta = record.get("metadata")
    if isinstance(meta, dict):
        return dict(meta)
    return {}


def _record_tags(record: dict[str, Any]) -> list[str]:
    """Return the ``cw_tags`` list stamped into a memory's metadata."""
    raw = _record_metadata(record).get(_CW_TAGS_FIELD)
    if isinstance(raw, list):
        return [t for t in raw if isinstance(t, str)]
    return []


class Mem0EpisodicStore:
    """:class:`~contextweaver.store.protocols.EpisodicStore` backed by Mem0.

    The store delegates similarity search to :meth:`mem0.Memory.search`
    and writes go through :meth:`mem0.Memory.add` with ``infer=False``
    so the text is stored as-supplied.  Every episode is stamped with
    ``cw_episode_id`` in its metadata so :meth:`get` and
    :meth:`delete` can resolve the canonical ID back to mem0's
    internal UUID.

    Args:
        memory: A configured :class:`mem0.Memory` instance.  Bring your
            own — the adapter does not configure the LLM / vector
            store backend.
        user_id: The mem0 session id used to scope every operation.
            Mem0 requires one of ``user_id`` / ``agent_id`` / ``run_id``
            on every call; this adapter exposes ``user_id`` because it
            matches the typical agent-deployment shape.  Use a stable
            value per agent / per tenant.
        scan_limit: Upper bound on the number of records pulled from
            :meth:`mem0.Memory.get_all` for a single :meth:`get`,
            :meth:`delete`, or :meth:`all` call.  When the configured
            ``user_id`` scope exceeds this number, methods that scan
            raise :class:`NotImplementedError`.  Defaults to ``1000``.
    """

    def __init__(
        self,
        memory: Memory,
        *,
        user_id: str,
        scan_limit: int = _DEFAULT_SCAN_LIMIT,
    ) -> None:
        if not user_id:
            raise Mem0BackendError("Mem0EpisodicStore requires a non-empty user_id.")
        self._memory = memory
        self._user_id = user_id
        self._scan_limit = scan_limit

    def add(self, episode: Episode) -> None:
        """Persist *episode* into the mem0 store under the configured scope."""
        metadata: dict[str, Any] = dict(episode.metadata)
        metadata[_CW_EPISODE_KEY] = episode.episode_id
        metadata[_CW_TAGS_FIELD] = list(episode.tags)
        self._memory.add(
            episode.summary,
            user_id=self._user_id,
            metadata=metadata,
            infer=False,
        )

    def _scan_records(self) -> list[dict[str, Any]]:
        """Return every record under the configured scope.

        Raises:
            NotImplementedError: When the scope exceeds ``scan_limit``
                — see the class docstring for the rationale.
        """
        raw = self._memory.get_all(filters={"user_id": self._user_id}, top_k=self._scan_limit)
        records = _normalise_get_all_response(raw)
        if len(records) >= self._scan_limit:
            raise NotImplementedError(
                f"Mem0EpisodicStore: {self._user_id!r} scope has at least "
                f"{self._scan_limit} memories; scanning ops are no longer "
                "lossless. Narrow scope via user_id partitioning or use "
                "a dedicated EpisodicStore backend."
            )
        return records

    def _record_for_episode(self, episode_id: str) -> dict[str, Any] | None:
        for record in self._scan_records():
            if _record_metadata(record).get(_CW_EPISODE_KEY) == episode_id:
                return record
        return None

    def get(self, episode_id: str) -> Episode | None:
        """Return the :class:`Episode` with ``episode_id`` or ``None``."""
        record = self._record_for_episode(episode_id)
        if record is None:
            return None
        return Episode(
            episode_id=episode_id,
            summary=_memory_text(record),
            tags=_record_tags(record),
            metadata={
                k: v
                for k, v in _record_metadata(record).items()
                if k not in (_CW_EPISODE_KEY, _CW_TAGS_FIELD)
            },
        )

    def search(self, query: str, top_k: int = 5) -> list[Episode]:
        """Return up to ``top_k`` episodes most relevant to *query*.

        Delegates to :meth:`mem0.Memory.search`.
        """
        raw = self._memory.search(
            query,
            top_k=top_k,
            filters={"user_id": self._user_id},
        )
        results = _normalise_get_all_response(raw)
        out: list[Episode] = []
        for record in results:
            meta = _record_metadata(record)
            ep_id = meta.get(_CW_EPISODE_KEY)
            if not isinstance(ep_id, str) or not ep_id:
                continue
            out.append(
                Episode(
                    episode_id=ep_id,
                    summary=_memory_text(record),
                    tags=_record_tags(record),
                    metadata={
                        k: v for k, v in meta.items() if k not in (_CW_EPISODE_KEY, _CW_TAGS_FIELD)
                    },
                )
            )
        return out

    def all(self) -> list[Episode]:
        """Return every episode under the configured scope, insertion-ordered."""
        records = self._scan_records()
        out: list[Episode] = []
        for record in records:
            meta = _record_metadata(record)
            ep_id = meta.get(_CW_EPISODE_KEY)
            if not isinstance(ep_id, str) or not ep_id:
                continue
            out.append(
                Episode(
                    episode_id=ep_id,
                    summary=_memory_text(record),
                    tags=_record_tags(record),
                    metadata={
                        k: v for k, v in meta.items() if k not in (_CW_EPISODE_KEY, _CW_TAGS_FIELD)
                    },
                )
            )
        return out

    def latest(self, n: int = 3) -> list[tuple[str, str, dict[str, Any]]]:
        """Return the *n* most recently added episodes, most-recent first.

        Mem0 returns records insertion-ordered; we reverse the tail.
        """
        if n <= 0:
            return []
        episodes = self.all()
        recent = episodes[-n:]
        return [(ep.episode_id, ep.summary, dict(ep.metadata)) for ep in reversed(recent)]

    def delete(self, episode_id: str) -> None:
        """Remove the episode with ``episode_id``.

        Raises:
            ItemNotFoundError: When no episode with ``episode_id`` is
                stored under the configured scope.
        """
        record = self._record_for_episode(episode_id)
        if record is None:
            raise ItemNotFoundError(f"Episode not found: {episode_id!r}")
        mem_id = _memory_id(record)
        if mem_id is None:
            raise Mem0BackendError(
                f"Mem0 record for episode {episode_id!r} is missing an 'id' field."
            )
        self._memory.delete(mem_id)


class Mem0FactStore:
    """:class:`~contextweaver.store.protocols.FactStore` backed by Mem0.

    See the :mod:`module docstring <contextweaver.extras.memory.mem0>` for
    the metadata-stamping strategy and the scan-limit semantics that
    apply equally to facts.

    Args:
        memory: A configured :class:`mem0.Memory` instance.
        user_id: Mem0 session id used to scope every operation.
        scan_limit: Upper bound on records scanned per
            :meth:`get` / :meth:`get_by_key` / :meth:`list_keys` /
            :meth:`all` / :meth:`delete` call.
    """

    def __init__(
        self,
        memory: Memory,
        *,
        user_id: str,
        scan_limit: int = _DEFAULT_SCAN_LIMIT,
    ) -> None:
        if not user_id:
            raise Mem0BackendError("Mem0FactStore requires a non-empty user_id.")
        self._memory = memory
        self._user_id = user_id
        self._scan_limit = scan_limit

    def put(self, fact: Fact) -> None:
        """Insert or replace the fact identified by ``fact.fact_id``."""
        existing = self._record_for_fact(fact.fact_id)
        if existing is not None:
            mem_id = _memory_id(existing)
            if mem_id is not None:
                self._memory.delete(mem_id)
        metadata: dict[str, Any] = dict(fact.metadata)
        metadata[_CW_FACT_KEY] = fact.fact_id
        metadata[_CW_FACT_KEY_FIELD] = fact.key
        metadata[_CW_TAGS_FIELD] = list(fact.tags)
        self._memory.add(
            fact.value,
            user_id=self._user_id,
            metadata=metadata,
            infer=False,
        )

    def _scan_records(self) -> list[dict[str, Any]]:
        raw = self._memory.get_all(filters={"user_id": self._user_id}, top_k=self._scan_limit)
        records = _normalise_get_all_response(raw)
        if len(records) >= self._scan_limit:
            raise NotImplementedError(
                f"Mem0FactStore: {self._user_id!r} scope has at least "
                f"{self._scan_limit} memories; scanning ops are no longer "
                "lossless. Narrow scope via user_id partitioning or use "
                "a dedicated FactStore backend."
            )
        return records

    def _record_for_fact(self, fact_id: str) -> dict[str, Any] | None:
        for record in self._scan_records():
            if _record_metadata(record).get(_CW_FACT_KEY) == fact_id:
                return record
        return None

    def _fact_from_record(self, record: dict[str, Any]) -> Fact | None:
        meta = _record_metadata(record)
        fact_id = meta.get(_CW_FACT_KEY)
        key = meta.get(_CW_FACT_KEY_FIELD)
        if not isinstance(fact_id, str) or not fact_id:
            return None
        if not isinstance(key, str):
            key = ""
        return Fact(
            fact_id=fact_id,
            key=key,
            value=_memory_text(record),
            tags=_record_tags(record),
            metadata={
                k: v
                for k, v in meta.items()
                if k not in (_CW_FACT_KEY, _CW_FACT_KEY_FIELD, _CW_TAGS_FIELD)
            },
        )

    def get(self, fact_id: str) -> Fact:
        """Return the fact with ``fact_id``.

        Raises:
            ItemNotFoundError: When no fact with ``fact_id`` is stored
                under the configured scope.
        """
        record = self._record_for_fact(fact_id)
        if record is None:
            raise ItemNotFoundError(f"Fact not found: {fact_id!r}")
        fact = self._fact_from_record(record)
        if fact is None:
            raise ItemNotFoundError(f"Fact not found: {fact_id!r}")
        return fact

    def get_by_key(self, key: str) -> list[Fact]:
        """Return every fact whose ``key`` matches *key*, sorted by ``fact_id``."""
        out: list[Fact] = []
        for record in self._scan_records():
            meta = _record_metadata(record)
            if meta.get(_CW_FACT_KEY_FIELD) != key:
                continue
            fact = self._fact_from_record(record)
            if fact is not None:
                out.append(fact)
        out.sort(key=lambda f: f.fact_id)
        return out

    def list_keys(self, prefix: str = "") -> list[str]:
        """Return every distinct fact key under the configured scope."""
        keys: set[str] = set()
        for record in self._scan_records():
            key = _record_metadata(record).get(_CW_FACT_KEY_FIELD)
            if isinstance(key, str) and key.startswith(prefix):
                keys.add(key)
        return sorted(keys)

    def delete(self, fact_id: str) -> None:
        """Remove the fact identified by ``fact_id``.

        Raises:
            ItemNotFoundError: When no fact with ``fact_id`` is stored
                under the configured scope.
        """
        record = self._record_for_fact(fact_id)
        if record is None:
            raise ItemNotFoundError(f"Fact not found: {fact_id!r}")
        mem_id = _memory_id(record)
        if mem_id is None:
            raise Mem0BackendError(f"Mem0 record for fact {fact_id!r} is missing an 'id' field.")
        self._memory.delete(mem_id)

    def all(self) -> list[Fact]:
        """Return every fact under the configured scope, sorted by ``fact_id``."""
        out: list[Fact] = []
        for record in self._scan_records():
            fact = self._fact_from_record(record)
            if fact is not None:
                out.append(fact)
        out.sort(key=lambda f: f.fact_id)
        return out
