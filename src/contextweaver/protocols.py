"""Protocol definitions and no-op default implementations for contextweaver.

Downstream code should depend on the protocols, not the concrete defaults,
so that stores, hooks, and summarisers remain swappable.

Store-layer protocols (:class:`EventLog`, :class:`ArtifactStore`,
:class:`EpisodicStore`, :class:`FactStore`) live in
:mod:`contextweaver.store.protocols` and are re-exported here for backward
compatibility — keep using ``from contextweaver.protocols import …`` if you
prefer the historical path.
"""

from __future__ import annotations

import logging as _logging
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import tiktoken as _tiktoken

from contextweaver.store.protocols import ArtifactStore as ArtifactStore
from contextweaver.store.protocols import EpisodicStore as EpisodicStore
from contextweaver.store.protocols import EventLog as EventLog
from contextweaver.store.protocols import FactStore as FactStore

if TYPE_CHECKING:
    from contextweaver.envelope import ContextPack
    from contextweaver.types import ContextItem, SelectableItem


_tiktoken_logger = _logging.getLogger("contextweaver.protocols")


# ---------------------------------------------------------------------------
# TokenEstimator
# ---------------------------------------------------------------------------


@runtime_checkable
class TokenEstimator(Protocol):
    """Estimate the number of tokens in a text string."""

    def estimate(self, text: str) -> int:
        """Return the estimated token count for *text*."""
        ...


class CharDivFourEstimator:
    """Simple heuristic: token count ≈ len(text) // 4."""

    def estimate(self, text: str) -> int:
        """Return ``len(text) // 4`` as a rough token estimate."""
        return len(text) // 4


class TiktokenEstimator:
    """Token estimator backed by OpenAI's ``tiktoken`` library.

    *model* may be a model name (e.g. ``"gpt-4"``) or a raw encoding name
    (e.g. ``"cl100k_base"``). Model names are resolved via
    ``tiktoken.encoding_for_model``; if that fails the value is treated as
    an encoding name.

    ``tiktoken`` is a core runtime dependency, so the import always succeeds.
    However, tiktoken downloads BPE encoding files on first use; in offline /
    air-gapped environments this download fails. When that happens the
    estimator transparently falls back to :class:`CharDivFourEstimator` and
    logs a warning so the operator can pre-cache encodings (set
    ``TIKTOKEN_CACHE_DIR``) or use the heuristic estimator directly.
    """

    def __init__(self, model: str = "cl100k_base") -> None:
        self._fallback: CharDivFourEstimator | None = None
        try:
            self._enc = _tiktoken.encoding_for_model(model)
        except KeyError:
            try:
                self._enc = _tiktoken.get_encoding(model)
            except Exception as exc:  # pragma: no cover - exercised in offline tests
                self._fallback = CharDivFourEstimator()
                _tiktoken_logger.warning(
                    "tiktoken encoding %r unavailable (%s); falling back to "
                    "CharDivFourEstimator. Pre-cache encodings via TIKTOKEN_CACHE_DIR "
                    "for exact token counts.",
                    model,
                    exc,
                )
        except Exception as exc:  # pragma: no cover - exercised in offline tests
            self._fallback = CharDivFourEstimator()
            _tiktoken_logger.warning(
                "tiktoken encoding %r unavailable (%s); falling back to "
                "CharDivFourEstimator. Pre-cache encodings via TIKTOKEN_CACHE_DIR "
                "for exact token counts.",
                model,
                exc,
            )

    def estimate(self, text: str) -> int:
        """Return the exact token count using tiktoken (or fallback estimate)."""
        if self._fallback is not None:
            return self._fallback.estimate(text)
        return len(self._enc.encode(text))


# ---------------------------------------------------------------------------
# EventHook
# ---------------------------------------------------------------------------


@runtime_checkable
class EventHook(Protocol):
    """Lifecycle callbacks fired by the Context Engine during a build pass."""

    def on_context_built(self, pack: ContextPack) -> None:
        """Called after a :class:`~contextweaver.types.ContextPack` is assembled."""
        ...

    def on_firewall_triggered(self, item: ContextItem, reason: str) -> None:
        """Called when a raw tool output is intercepted by the context firewall."""
        ...

    def on_items_excluded(self, items: list[ContextItem], reason: str) -> None:
        """Called when items are dropped from the context (budget / policy)."""
        ...

    def on_budget_exceeded(self, requested: int, budget: int) -> None:
        """Called when a build exceeds the configured token budget."""
        ...

    def on_route_completed(self, tool_ids: list[str]) -> None:
        """Called after the router produces a route through the choice graph."""
        ...


class NoOpHook:
    """Default no-op implementation of :class:`EventHook`."""

    def on_context_built(self, pack: ContextPack) -> None:
        """No-op."""

    def on_firewall_triggered(self, item: ContextItem, reason: str) -> None:
        """No-op."""

    def on_items_excluded(self, items: list[ContextItem], reason: str) -> None:
        """No-op."""

    def on_budget_exceeded(self, requested: int, budget: int) -> None:
        """No-op."""

    def on_route_completed(self, tool_ids: list[str]) -> None:
        """No-op."""


# ---------------------------------------------------------------------------
# Summarizer / Extractor
# ---------------------------------------------------------------------------


# FUTURE: LLM-backed summarizer/extractor for higher-quality summarization.


@runtime_checkable
class Summarizer(Protocol):
    """Convert a raw tool output string into a human/LLM-readable summary."""

    def summarize(self, raw: str, metadata: dict[str, Any]) -> str:
        """Return a summary of *raw* given optional *metadata* context."""
        ...


@runtime_checkable
class Extractor(Protocol):
    """Extract structured facts from a raw tool output."""

    def extract(self, raw: str, metadata: dict[str, Any]) -> list[str]:
        """Return a list of fact strings extracted from *raw*."""
        ...


# ---------------------------------------------------------------------------
# RedactionHook
# ---------------------------------------------------------------------------


@runtime_checkable
class RedactionHook(Protocol):
    """Apply redaction rules to a :class:`~contextweaver.types.ContextItem`."""

    def redact(self, item: ContextItem) -> ContextItem:
        """Return a (possibly modified) copy of *item* with sensitive data removed."""
        ...


# ---------------------------------------------------------------------------
# Labeler
# ---------------------------------------------------------------------------


# FUTURE: LLM-backed labeler that calls an LLM for category assignment.


@runtime_checkable
class Labeler(Protocol):
    """Assign a category label and confidence score to a SelectableItem."""

    def label(self, item: SelectableItem) -> tuple[str, str]:
        """Return ``(category, confidence)`` for *item*."""
        ...


# ---------------------------------------------------------------------------
# Routing engines (Retriever / Reranker / ClusteringEngine)
# ---------------------------------------------------------------------------


@runtime_checkable
class Retriever(Protocol):
    """Pluggable first-stage retriever.

    A retriever scores a fixed corpus of documents against a query and
    returns the indices of the top-k most relevant documents.  It is the
    routing engine's plug-point for swapping TF-IDF, BM25, embedding-based
    ANN, or hybrid backends.

    Implementations must be deterministic in
    :class:`~contextweaver.config.Mode.strict`: identical (corpus, query,
    top_k) inputs must produce identical outputs.
    """

    def fit(self, corpus: list[str]) -> None:
        """Index *corpus* once before any :meth:`search` or :meth:`score_one` call.

        May be called repeatedly with new corpora; the latest call wins.
        """
        ...

    def search(self, query: str, top_k: int) -> list[tuple[int, float]]:
        """Return up to *top_k* ``(corpus_index, score)`` pairs.

        Higher scores rank first.  Implementations should break score
        ties by ascending corpus index for determinism.
        """
        ...

    def score_one(self, query: str, index: int) -> float:
        """Return the score for the corpus document at *index* against *query*.

        Used by callers (e.g. :class:`~contextweaver.routing.router.Router`
        beam search) that must score arbitrary corpus indices outside the
        top-k window.  Implementations must agree with :meth:`search`:
        the score returned here must match the score that document would
        have received under :meth:`search` for the same *query*.

        Implementations should return ``0.0`` for out-of-range indices
        or when the index has not been fit.
        """
        ...


@runtime_checkable
class Reranker(Protocol):
    """Optional second-stage reranker.

    A reranker takes the retriever's shortlist and re-orders it using a
    more expensive scoring function (e.g. a cross-encoder, an LLM, or a
    rule-based heuristic).  Returning the input unchanged is valid and
    is the default behaviour of :class:`NoOpReranker`.
    """

    def rerank(
        self,
        query: str,
        candidates: list[tuple[str, float]],
    ) -> list[tuple[str, float]]:
        """Re-score *candidates* and return them in new ranking order."""
        ...


@runtime_checkable
class ClusteringEngine(Protocol):
    """Pluggable clustering engine for :class:`TreeBuilder`.

    A clustering engine groups items into roughly equal-sized clusters
    based on a pairwise similarity / distance signal.  The default
    implementation in ``TreeBuilder`` uses Jaccard farthest-first seeding;
    swapping in :class:`Retriever`-derived embeddings is the canonical
    use case for this protocol.
    """

    def cluster(
        self,
        items: list[SelectableItem],
        *,
        k: int,
    ) -> dict[str, list[SelectableItem]]:
        """Partition *items* into at most *k* clusters.

        Returns a dict whose keys are cluster labels (e.g.
        ``"cluster_000"``) and values are the items assigned to that
        cluster.  Implementations may return fewer than *k* clusters
        when the data is degenerate.
        """
        ...
