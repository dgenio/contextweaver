"""Zep / Graphiti ``EpisodicStore`` / ``FactStore`` backend (issue #195).

Adapts the [Zep Cloud](https://www.getzep.com/) Python client (``zep-cloud``)
so an existing Zep deployment can back contextweaver's optional long-lived
stores.  The two classes implement the existing
:class:`~contextweaver.store.protocols.EpisodicStore` and
:class:`~contextweaver.store.protocols.FactStore` Protocols verbatim (the
Protocols are not widened).

Requires the ``[zep]`` optional extra (``pip install 'contextweaver[zep]'``);
without it, importing this module raises :class:`ImportError` with that hint.
Shared constants, payload helpers, and the store base class live in
:mod:`contextweaver.extras.memory._zep_common` to keep this module cohesive.

**Persistence.** Zep extracts edges/nodes from **episodes** (the raw inputs you
add); episodes are the one surface that round-trips the exact input, so this
adapter uses them as the lossless system of record.  Each
:class:`~contextweaver.store.episodic.Episode` and
:class:`~contextweaver.store.facts.Fact` is written via
:meth:`graph.add(type="json") <zep_cloud.Zep.graph>` as a JSON episode embedding
the canonical ID (``cw_episode_id`` / ``cw_fact_id``) and a ``cw_kind``
discriminator; reads scan :meth:`graph.episode.get_by_user_id` and resolve that
ID back to Zep's episode ``uuid_``, staying lossless and deterministic.

**Out of scope (this cycle).** Zep's native semantic recall
(:meth:`graph.search`) operates over extracted edges/nodes, which do not map
onto the Episode/Fact key-value contract, so :meth:`EpisodicStore.search` does a
deterministic client-side match over the persisted episodes instead (widening
the search Protocol is a tracked follow-up).  When the ``user_id`` scope holds
more than ``scan_limit`` episodes the scanning methods raise
:class:`NotImplementedError` rather than truncating.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from contextweaver.exceptions import ItemNotFoundError
from contextweaver.extras.memory._zep_common import (
    _CW_EPISODE_ID,
    _CW_FACT_ID,
    _CW_FACT_KEY,
    _CW_KIND,
    _DEFAULT_SCAN_LIMIT,
    _KIND_EPISODE,
    _KIND_FACT,
    ZepBackendError,
    _coerce_metadata,
    _coerce_str_tags,
    _episode_payload,
    _episode_uuid,
    _ZepStoreBase,
)
from contextweaver.store.episodic import Episode
from contextweaver.store.facts import Fact

if TYPE_CHECKING:
    from zep_cloud.client import Zep


class ZepEpisodicStore(_ZepStoreBase):
    """:class:`~contextweaver.store.protocols.EpisodicStore` backed by Zep.

    Args:
        client: A configured :class:`zep_cloud.client.Zep` instance.  Bring
            your own — the adapter does not configure the deployment.
        user_id: Zep user id used to scope every operation.  Use a stable
            value per agent / per tenant.
        scan_limit: Upper bound on episodes pulled per scanning op; the scope
            raises :class:`NotImplementedError` once it reaches this many.
            Defaults to ``1000``.
    """

    def __init__(
        self,
        client: Zep,
        *,
        user_id: str,
        scan_limit: int = _DEFAULT_SCAN_LIMIT,
    ) -> None:
        super().__init__(client, user_id=user_id, scan_limit=scan_limit)

    def _record_for(self, episode_id: str) -> object | None:
        for record in self._scan():
            payload = _episode_payload(record)
            if payload.get(_CW_KIND) == _KIND_EPISODE and payload.get(_CW_EPISODE_ID) == episode_id:
                return record
        return None

    @staticmethod
    def _to_episode(payload: dict[str, Any]) -> Episode:
        return Episode(
            episode_id=str(payload.get(_CW_EPISODE_ID, "")),
            summary=str(payload.get("summary", "")),
            tags=_coerce_str_tags(payload.get("tags")),
            metadata=_coerce_metadata(payload.get("metadata")),
        )

    def add(self, episode: Episode) -> None:
        """Persist *episode* as a JSON episode (upsert by ``episode_id``).

        When the scope exceeds ``scan_limit`` the duplicate check is skipped
        and the episode is appended unconditionally (append-only fallback).
        """
        try:
            existing = self._record_for(episode.episode_id)
        except NotImplementedError:
            existing = None
        if existing is not None:
            uuid = _episode_uuid(existing)
            if uuid is not None:
                self._delete_uuid(uuid)
        self._add_json(
            {
                _CW_KIND: _KIND_EPISODE,
                _CW_EPISODE_ID: episode.episode_id,
                "summary": episode.summary,
                "tags": list(episode.tags),
                "metadata": dict(episode.metadata),
            }
        )

    def get(self, episode_id: str) -> Episode | None:
        """Return the episode with ``episode_id`` or ``None``."""
        record = self._record_for(episode_id)
        return None if record is None else self._to_episode(_episode_payload(record))

    def _all_payloads(self) -> list[dict[str, Any]]:
        return [
            p
            for p in (_episode_payload(r) for r in self._scan())
            if p.get(_CW_KIND) == _KIND_EPISODE and p.get(_CW_EPISODE_ID)
        ]

    def search(self, query: str, top_k: int = 5) -> list[Episode]:
        """Return up to ``top_k`` episodes whose summary matches *query*.

        Deterministic client-side match (see the module docstring for why
        Zep's edge/node ``graph.search`` is not used here): ranks by the number
        of query terms present in the summary, ties broken by ``episode_id``.
        """
        terms = [t for t in query.lower().split() if t]
        scored: list[tuple[int, str, dict[str, Any]]] = []
        for payload in self._all_payloads():
            summary = str(payload.get("summary", "")).lower()
            score = sum(1 for t in terms if t in summary)
            if score > 0:
                scored.append((score, str(payload.get(_CW_EPISODE_ID)), payload))
        scored.sort(key=lambda s: (-s[0], s[1]))
        return [self._to_episode(p) for _, _, p in scored[:top_k]]

    def all(self) -> list[Episode]:
        """Return every episode under the configured scope, scan-ordered."""
        return [self._to_episode(p) for p in self._all_payloads()]

    def latest(self, n: int = 3) -> list[tuple[str, str, dict[str, Any]]]:
        """Return the *n* most recently added episodes, most-recent first."""
        if n <= 0:
            return []
        recent = list(reversed(self._all_payloads()))[:n]
        return [
            (
                str(p.get(_CW_EPISODE_ID)),
                str(p.get("summary", "")),
                _coerce_metadata(p.get("metadata")),
            )
            for p in recent
        ]

    def delete(self, episode_id: str) -> None:
        """Remove the episode with ``episode_id``.

        Raises:
            ItemNotFoundError: When no such episode is stored under the scope.
        """
        record = self._record_for(episode_id)
        if record is None:
            raise ItemNotFoundError(f"Episode not found: {episode_id!r}")
        uuid = _episode_uuid(record)
        if uuid is None:
            raise ZepBackendError(f"Zep episode for {episode_id!r} is missing a uuid.")
        self._delete_uuid(uuid)


class ZepFactStore(_ZepStoreBase):
    """:class:`~contextweaver.store.protocols.FactStore` backed by Zep.

    See the :mod:`module docstring <contextweaver.extras.memory.zep>` for the
    JSON-episode storage strategy and scan-limit semantics that apply equally
    to facts.

    Args:
        client: A configured :class:`zep_cloud.client.Zep` instance.
        user_id: Zep user id used to scope every operation.
        scan_limit: Upper bound on episodes scanned per op.
    """

    def __init__(
        self,
        client: Zep,
        *,
        user_id: str,
        scan_limit: int = _DEFAULT_SCAN_LIMIT,
    ) -> None:
        super().__init__(client, user_id=user_id, scan_limit=scan_limit)

    @staticmethod
    def _to_fact(payload: dict[str, Any]) -> Fact:
        return Fact(
            fact_id=str(payload.get(_CW_FACT_ID, "")),
            key=str(payload.get(_CW_FACT_KEY, "")),
            value=str(payload.get("value", "")),
            tags=_coerce_str_tags(payload.get("tags")),
            metadata=_coerce_metadata(payload.get("metadata")),
        )

    def _fact_payloads(self) -> list[dict[str, Any]]:
        return [
            p
            for p in (_episode_payload(r) for r in self._scan())
            if p.get(_CW_KIND) == _KIND_FACT and p.get(_CW_FACT_ID)
        ]

    def _record_for(self, fact_id: str) -> object | None:
        for record in self._scan():
            payload = _episode_payload(record)
            if payload.get(_CW_KIND) == _KIND_FACT and payload.get(_CW_FACT_ID) == fact_id:
                return record
        return None

    def put(self, fact: Fact) -> None:
        """Insert or replace the fact identified by ``fact.fact_id``.

        When the scope exceeds ``scan_limit`` the duplicate check is skipped
        and the fact is appended unconditionally (append-only fallback).
        """
        try:
            existing = self._record_for(fact.fact_id)
        except NotImplementedError:
            existing = None
        if existing is not None:
            uuid = _episode_uuid(existing)
            if uuid is not None:
                self._delete_uuid(uuid)
        self._add_json(
            {
                _CW_KIND: _KIND_FACT,
                _CW_FACT_ID: fact.fact_id,
                _CW_FACT_KEY: fact.key,
                "value": fact.value,
                "tags": list(fact.tags),
                "metadata": dict(fact.metadata),
            }
        )

    def get(self, fact_id: str) -> Fact:
        """Return the fact with ``fact_id``.

        Raises:
            ItemNotFoundError: When no such fact is stored under the scope.
        """
        record = self._record_for(fact_id)
        if record is None:
            raise ItemNotFoundError(f"Fact not found: {fact_id!r}")
        return self._to_fact(_episode_payload(record))

    def get_by_key(self, key: str) -> list[Fact]:
        """Return every fact whose ``key`` matches *key*, sorted by ``fact_id``."""
        out = [self._to_fact(p) for p in self._fact_payloads() if p.get(_CW_FACT_KEY) == key]
        out.sort(key=lambda f: f.fact_id)
        return out

    def list_keys(self, prefix: str = "") -> list[str]:
        """Return every distinct fact key under the scope, optionally prefix-filtered."""
        keys = {
            str(p.get(_CW_FACT_KEY, ""))
            for p in self._fact_payloads()
            if str(p.get(_CW_FACT_KEY, "")).startswith(prefix)
        }
        return sorted(keys)

    def delete(self, fact_id: str) -> None:
        """Remove the fact identified by ``fact_id``.

        Raises:
            ItemNotFoundError: When no such fact is stored under the scope.
        """
        record = self._record_for(fact_id)
        if record is None:
            raise ItemNotFoundError(f"Fact not found: {fact_id!r}")
        uuid = _episode_uuid(record)
        if uuid is None:
            raise ZepBackendError(f"Zep episode for fact {fact_id!r} is missing a uuid.")
        self._delete_uuid(uuid)

    def all(self) -> list[Fact]:
        """Return every fact under the configured scope, sorted by ``fact_id``."""
        out = [self._to_fact(p) for p in self._fact_payloads()]
        out.sort(key=lambda f: f.fact_id)
        return out
