"""Corpus fingerprinting + retriever serialisation for the index cache.

Private helper for :mod:`contextweaver.routing.index_cache` (keeps that module
within its size ceiling).  Holds the deterministic corpus fingerprint and the
:class:`IndexCodec` contract plus the bundled TF-IDF codec.  Not public API —
the public names are re-exported from ``index_cache``.
"""

from __future__ import annotations

import hashlib
from typing import Any, Protocol, runtime_checkable

from contextweaver.exceptions import ConfigError


def index_fingerprint(documents: list[str], *, engine_name: str) -> str:
    """Return a deterministic content fingerprint for a fitted corpus.

    Folds in *engine_name* and every document in corpus order (order is
    significant — it defines the ``doc_index`` identity the retriever scores
    against), so distinct backends never share an entry and a reordered corpus
    is a distinct key.

    Every field is **length-prefixed** (8-byte big-endian) before its bytes,
    so the encoding is unambiguous even if a document contains embedded NUL
    bytes or shares boundaries with its neighbours — two different document
    lists can never serialise to the same byte stream and collide.

    Args:
        documents: The ordered corpus passed to :meth:`Retriever.fit`.
        engine_name: Stable identifier of the retriever backend (``"tfidf"``).

    Returns:
        A hex-encoded SHA-256 digest.
    """
    hasher = hashlib.sha256()
    name_bytes = engine_name.encode("utf-8")
    hasher.update(len(name_bytes).to_bytes(8, "big"))
    hasher.update(name_bytes)
    hasher.update(len(documents).to_bytes(8, "big"))
    for doc in documents:
        doc_bytes = doc.encode("utf-8")
        hasher.update(len(doc_bytes).to_bytes(8, "big"))
        hasher.update(doc_bytes)
    return hasher.hexdigest()


@runtime_checkable
class IndexCodec(Protocol):
    """Serialise / restore a retriever's fitted state to a JSON-safe dict.

    Bridges a concrete :class:`~contextweaver.protocols.Retriever` backend and
    the index cache.  Implementations must round-trip exactly:
    ``load(r, dump(r))`` leaves ``r`` scoring byte-identically.
    """

    #: Stable backend identifier; matches the corpus ``engine_name``.
    name: str
    #: Codec payload version; a mismatch is treated as a cache miss.
    version: int

    def dump(self, retriever: Any) -> dict[str, Any]:  # noqa: ANN401 - codec pokes backend internals
        """Return a deterministic JSON-compatible snapshot of *retriever*."""
        ...

    def load(self, retriever: Any, state: dict[str, Any]) -> None:  # noqa: ANN401 - see dump
        """Restore *retriever*'s fitted state from a :meth:`dump` snapshot."""
        ...


class _TfIdfCodec:
    """:class:`IndexCodec` for the bundled TF-IDF retriever.

    Captures the only state the scorer needs to reproduce scores — the
    per-document sorted token lists and the corpus IDF table.  These are
    internal attributes of
    :class:`~contextweaver.routing.registry.TfIdfRetriever` and its wrapped
    :class:`~contextweaver._utils.TfIdfScorer`; touching them here keeps the
    serialisation knowledge in one place rather than widening those frozen
    modules' public surface.
    """

    name = "tfidf"
    version = 1

    def dump(self, retriever: Any) -> dict[str, Any]:  # noqa: ANN401 - codec pokes backend internals
        scorer = getattr(retriever, "_scorer", None)
        if scorer is None:
            raise ConfigError("TF-IDF retriever has no fitted scorer to serialise")
        documents = [list(tokens) for tokens in scorer._documents]
        return {
            "documents": documents,
            "idf": {str(term): float(weight) for term, weight in scorer._idf.items()},
            "corpus_size": int(getattr(retriever, "_corpus_size", len(documents))),
        }

    def load(self, retriever: Any, state: dict[str, Any]) -> None:  # noqa: ANN401 - see dump
        from contextweaver._utils import TfIdfScorer

        scorer = TfIdfScorer()
        scorer._documents = [[str(tok) for tok in doc] for doc in state["documents"]]
        scorer._idf = {str(term): float(weight) for term, weight in state["idf"].items()}
        retriever._scorer = scorer
        retriever._corpus_size = int(state.get("corpus_size", len(scorer._documents)))


#: The default codec — serialises the bundled TF-IDF retriever.
TFIDF_CODEC: IndexCodec = _TfIdfCodec()
