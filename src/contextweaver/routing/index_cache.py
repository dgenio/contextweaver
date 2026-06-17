"""Persistent, reusable fitted-index cache for the Routing Engine.

Fitting the first-stage retriever (TF-IDF by default) over a large tool
catalog is the dominant cost of the *first* :meth:`Router.route` call: every
document is tokenised and the corpus IDF table is built once.  Deployments
that re-create :class:`~contextweaver.routing.router.Router` instances over
the same catalog — a fresh process per request, a worker pool, a CLI in a
loop — pay that cost repeatedly even though the fitted index is identical.

This module persists the fitted index keyed by a deterministic fingerprint of
the indexed corpus, alongside the in-memory
:class:`~contextweaver.routing.tree.TreeBuilder` graph cache (issue #15), so
the fit happens at most once per catalog per cache (issues #543 / #624 / #685):

* :class:`RoutingIndexCache` — content-addressed store with an in-process LRU
  layer (reuse across :class:`Router` instances in one process) and an
  optional on-disk layer (reuse across restarts), written as deterministic
  JSON via an atomic temp-file + ``os.replace``.
* :class:`CachedRetriever` — a :class:`~contextweaver.protocols.Retriever`
  wrapper that consults the cache on :meth:`fit` and stores the fitted state
  on a miss; :meth:`search` / :meth:`score_one` delegate unchanged.

The cache is *transparent*: a warm load reproduces byte-identical scores to a
cold fit (pinned by ``tests/test_routing_quality_guardrails.py``) and never
raises into the routing path — a corrupt, unreadable, or version-incompatible
payload is treated as a miss and the index is re-fitted.  Only the bundled
TF-IDF retriever is serialisable out of the box via :data:`TFIDF_CODEC`;
custom retrievers can supply their own :class:`IndexCodec`.  Fingerprinting
and the codec contract live in the private ``_index_codec`` helper.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from collections import OrderedDict
from pathlib import Path
from typing import Any

from contextweaver.protocols import Retriever
from contextweaver.routing._index_codec import (
    TFIDF_CODEC,
    IndexCodec,
    index_fingerprint,
)

logger = logging.getLogger("contextweaver.routing")

#: Cache payload schema version.  Bumped when the on-disk envelope shape
#: changes incompatibly; older payloads then read as misses.
CACHE_ENVELOPE_VERSION: int = 1

#: Default in-process LRU capacity so a long-lived process churning catalogs
#: cannot grow the map without bound.
_DEFAULT_MAX_ENTRIES = 128


class RoutingIndexCache:
    """Content-addressed cache for fitted retriever indices.

    Two layers: an in-process LRU dict (cross-instance reuse in one process,
    folding in issue #543) and, when *directory* is given, an on-disk layer
    that survives restarts (issue #624).  Files are deterministic JSON written
    atomically (temp file + ``os.replace``).  Lookups never raise on a damaged
    entry — an unreadable or malformed file reads as a miss so the caller
    re-fits.

    Args:
        directory: Optional directory for the persistent layer (created if
            absent).  ``None`` keeps the cache purely in-process.
        max_entries: In-process LRU capacity (default 128).
    """

    def __init__(
        self,
        directory: str | os.PathLike[str] | None = None,
        *,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
    ) -> None:
        self._dir = Path(directory) if directory is not None else None
        if self._dir is not None:
            self._dir.mkdir(parents=True, exist_ok=True)
        self._mem: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._max_entries = max(1, max_entries)
        #: Lookups served from either layer.
        self.hits = 0
        #: Lookups that found nothing.
        self.misses = 0

    def get(self, fingerprint: str) -> dict[str, Any] | None:
        """Return the cached envelope for *fingerprint*, or ``None`` on a miss."""
        payload = self._mem.get(fingerprint)
        if payload is not None:
            self._mem.move_to_end(fingerprint)
            self.hits += 1
            return payload
        if self._dir is not None:
            payload = self._read_disk(fingerprint)
            if payload is not None:
                self._remember(fingerprint, payload)
                self.hits += 1
                return payload
        self.misses += 1
        return None

    def put(self, fingerprint: str, payload: dict[str, Any]) -> None:
        """Store *payload* under *fingerprint* in both layers."""
        self._remember(fingerprint, payload)
        if self._dir is not None:
            self._write_disk(fingerprint, payload)

    def _remember(self, fingerprint: str, payload: dict[str, Any]) -> None:
        self._mem[fingerprint] = payload
        self._mem.move_to_end(fingerprint)
        while len(self._mem) > self._max_entries:
            self._mem.popitem(last=False)

    def _path_for(self, fingerprint: str) -> Path:
        assert self._dir is not None  # narrow: callers gate this on self._dir
        return self._dir / f"idx_{fingerprint}.json"

    def _read_disk(self, fingerprint: str) -> dict[str, Any] | None:
        path = self._path_for(fingerprint)
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:  # corrupt payload → miss
            logger.warning("routing index cache: ignoring corrupt entry %s (%s)", path.name, exc)
            return None
        return data if isinstance(data, dict) else None

    def _write_disk(self, fingerprint: str, payload: dict[str, Any]) -> None:
        path = self._path_for(fingerprint)
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        try:
            fd, tmp_name = tempfile.mkstemp(dir=str(self._dir), prefix=".idx_", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(text)
                os.replace(tmp_name, path)
            except OSError:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_name)
                raise
        except OSError as exc:  # disk full / permissions → skip persistence
            logger.warning("routing index cache: could not persist %s (%s)", path.name, exc)


class CachedRetriever:
    """Cache-backed :class:`~contextweaver.protocols.Retriever` wrapper.

    Wraps a *base* retriever and a :class:`RoutingIndexCache`.  On :meth:`fit`
    the corpus is fingerprinted; a hit restores the fitted state without
    re-tokenising, a miss fits *base* and stores the result.  :meth:`search`
    and :meth:`score_one` delegate to *base*, so scoring is identical to using
    *base* directly.

    Pass it to a router via ``Router(graph, items=items,
    retriever=CachedRetriever(TfIdfRetriever(), cache))``.

    Args:
        base: The retriever to wrap (the default :data:`TFIDF_CODEC` expects a
            TF-IDF retriever).
        cache: The :class:`RoutingIndexCache` to consult and populate.
        engine_name: Fingerprint backend id; defaults to the codec's
            :attr:`~contextweaver.routing._index_codec.IndexCodec.name`.
        codec: The :class:`IndexCodec` for *base*; defaults to
            :data:`TFIDF_CODEC`.
    """

    def __init__(
        self,
        base: Retriever,
        cache: RoutingIndexCache,
        *,
        engine_name: str | None = None,
        codec: IndexCodec | None = None,
    ) -> None:
        self._base = base
        self._cache = cache
        self._codec = codec or TFIDF_CODEC
        self._engine_name = engine_name or self._codec.name
        #: ``True`` after the most recent :meth:`fit` was served from cache.
        self.loaded_from_cache = False

    @property
    def base(self) -> Retriever:
        """The wrapped retriever (exposed for tests and custom pipelines)."""
        return self._base

    def fit(self, corpus: list[str]) -> None:
        """Fit from cache when possible, otherwise fit *base* and cache it."""
        fingerprint = index_fingerprint(corpus, engine_name=self._engine_name)
        envelope = self._cache.get(fingerprint)
        if envelope is not None and self._envelope_matches(envelope):
            try:
                self._codec.load(self._base, envelope["state"])
                self.loaded_from_cache = True
                return
            except Exception as exc:  # noqa: BLE001 - cache must never break routing
                logger.warning("routing index cache: load failed (%s); re-fitting", exc)
        self._base.fit(corpus)
        self.loaded_from_cache = False
        self._store(fingerprint)

    def _envelope_matches(self, envelope: dict[str, Any]) -> bool:
        return (
            envelope.get("codec") == self._codec.name
            and envelope.get("version") == self._codec.version
            and envelope.get("envelope_version") == CACHE_ENVELOPE_VERSION
            and isinstance(envelope.get("state"), dict)
        )

    def _store(self, fingerprint: str) -> None:
        try:
            state = self._codec.dump(self._base)
        except Exception as exc:  # noqa: BLE001 - never let caching break a fit
            logger.warning("routing index cache: dump failed (%s); not caching", exc)
            return
        self._cache.put(
            fingerprint,
            {
                "envelope_version": CACHE_ENVELOPE_VERSION,
                "codec": self._codec.name,
                "version": self._codec.version,
                "state": state,
            },
        )

    def search(self, query: str, top_k: int) -> list[tuple[int, float]]:
        """Delegate to the wrapped retriever."""
        return self._base.search(query, top_k)

    def score_one(self, query: str, index: int) -> float:
        """Delegate to the wrapped retriever."""
        return self._base.score_one(query, index)


__all__ = [
    "CACHE_ENVELOPE_VERSION",
    "CachedRetriever",
    "IndexCodec",
    "RoutingIndexCache",
    "TFIDF_CODEC",
    "index_fingerprint",
]
