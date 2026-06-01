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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import tiktoken as _tiktoken

from contextweaver.store.protocols import ArtifactStore as ArtifactStore
from contextweaver.store.protocols import EpisodicStore as EpisodicStore
from contextweaver.store.protocols import EventLog as EventLog
from contextweaver.store.protocols import FactStore as FactStore

if TYPE_CHECKING:
    from contextweaver.context.memory_source import MemoryEntry
    from contextweaver.envelope import ChoiceCard, ContextPack
    from contextweaver.routing.graph import ChoiceGraph
    from contextweaver.types import ContextItem, Phase, SelectableItem


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
# MemorySource (issue #293)
# ---------------------------------------------------------------------------


@runtime_checkable
class MemorySource(Protocol):
    """Pluggable source of persistent agent memory entries.

    A memory source produces
    :class:`~contextweaver.context.memory_source.MemoryEntry` records that can
    be ingested into the Context Engine as ``memory_fact``
    :class:`~contextweaver.types.ContextItem` candidates.  The Context Engine
    treats memory entries like any other event-log item once they are
    materialised — phase filtering, sensitivity enforcement, scoring,
    deduplication, and budget selection all apply unchanged.

    Implementations must be storage-agnostic in the first version: a memory
    source may be backed by a JSON file, an in-memory list, or a thin shim
    over an external long-lived backend (Mem0, Zep, LangMem — issue #195),
    but the protocol itself does not assume any of those.  Sensitivity
    metadata on entries must be preserved and is enforced by the existing
    :func:`~contextweaver.context.sensitivity.apply_sensitivity_filter` after
    materialisation.

    Determinism contract: for identical ``(query, phase, now, max_entries)``
    inputs against an unchanged backend, :meth:`select` must return entries
    in the same order.  Tie-break by entry ID, never insertion order.
    """

    def select(
        self,
        query: str,
        phase: Phase,
        *,
        now: float | None = None,
        max_entries: int | None = None,
    ) -> list[MemoryEntry]:
        """Return memory entries relevant to *query* under *phase*.

        Args:
            query: User / agent query string (already augmented if needed).
            phase: Active execution phase.  Implementations should bias
                selection toward entries whose ``scope`` matches the phase
                (e.g. ``routing`` for :attr:`~contextweaver.types.Phase.route`,
                ``domain`` / ``fact`` for
                :attr:`~contextweaver.types.Phase.interpret`).
            now: Optional UNIX timestamp used as the reference time for
                ``expires_at`` filtering.  ``None`` means "use the current
                wall clock"; tests should pin this for determinism.
            max_entries: Optional upper bound on the number of entries
                returned.  ``None`` means no cap (the caller is expected to
                apply a token budget downstream).

        Returns:
            A list of :class:`MemoryEntry` objects sorted in deterministic
            relevance order (best first; ties broken by ID).
        """
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


# ---------------------------------------------------------------------------
# RoutingScoreProvider (issue #318)
# ---------------------------------------------------------------------------


@runtime_checkable
class RoutingScoreProvider(Protocol):
    """Optional feedback-aware adjuster for routing scores (issue #318).

    A score provider takes the navigator's ranked ``(item_id, score)`` pairs
    and returns adjusted pairs — typically folding in historical execution
    signals (success rate, latency, token cost, result quality) via
    :class:`~contextweaver.routing.feedback.ExecutionFeedback`.  It is the
    routing engine's opt-in seam for *learned* or *feedback-aware* ranking;
    the default :class:`~contextweaver.routing.router.Router` applies no
    provider and stays purely deterministic.

    Implementations MUST be deterministic: identical ``(query, scored)``
    inputs must yield identical outputs, and ties must break by ascending
    ``item_id`` (re-sort by ``(-score, id)``), exactly like the rest of the
    routing engine.  The bundled
    :class:`~contextweaver.routing.feedback.DeterministicScoreProvider` is a
    no-op reference implementation;
    :class:`~contextweaver.routing.feedback.FeedbackAwareScoreProvider`
    applies bounded feedback deltas.
    """

    def adjust(
        self,
        query: str,
        scored: list[tuple[str, float]],
    ) -> list[tuple[str, float]]:
        """Return *scored* re-scored and re-ranked (ties broken by id)."""
        ...


# ---------------------------------------------------------------------------
# Navigator (issue #56)
# ---------------------------------------------------------------------------


@runtime_checkable
class Navigator(Protocol):
    """Pluggable graph-navigation stage of the routing pipeline.

    A navigator walks a :class:`~contextweaver.routing.graph.ChoiceGraph`
    and returns scored leaf items.  The bundled default
    :class:`~contextweaver.routing.navigator.BeamSearchNavigator` performs
    bounded beam search with a per-node :class:`Retriever` scorer; alternative
    implementations may use exhaustive search, A*, learned policies, or skip
    navigation entirely (single-level catalogs).

    Implementations must be deterministic in
    :class:`~contextweaver.profiles.Mode` ``strict``: identical inputs
    must yield identical outputs.  Tie-break by ID, never by insertion order.
    """

    def navigate(
        self,
        query: str,
        graph: ChoiceGraph,
        active_items: dict[str, SelectableItem],
        scorer: Retriever,
        doc_id_to_idx: dict[str, int],
        *,
        all_item_ids: set[str] | None = None,
        debug: bool = False,
    ) -> NavigationResult:
        """Walk *graph* and return scored ``(item_id, score, path)`` tuples.

        Args:
            query: The scoring query (already augmented by context hints).
            graph: The choice graph to walk.
            active_items: Post-filter catalog (``exclude_*`` / ``allowed_*``
                applied upstream).  Only IDs in this dict are eligible for
                collection.
            scorer: The :class:`Retriever` used to score individual nodes.
            doc_id_to_idx: Map of doc id → index in *scorer*'s fitted
                corpus.  Nodes outside this map fall back to id-token
                Jaccard inside the navigator (see ``BeamSearchNavigator``).
            all_item_ids: Optional full pre-filter set of catalog item IDs.
                Used to distinguish leaves (catalog items) from internal
                graph nodes — preserves the issue #112 / #22 pre-filter
                behaviour exactly.  ``None`` falls back to ``set(active_items)``.
            debug: When ``True``, the returned
                :class:`NavigationResult` populates ``steps`` with per-depth
                beam expansions (for ``RouteTrace``).

        Returns:
            A :class:`NavigationResult` carrying the collected item map
            (``id -> (score, path)``) plus optional debug trace steps.
        """
        ...


@dataclass
class NavigationResult:
    """Output of a :class:`Navigator`.

    Attributes:
        collected: Map of ``item_id`` → ``(score, path)``.  Untrimmed —
            ranking and ``top_k`` truncation happen in the pipeline.
        steps: Per-depth beam-expansion records.  Empty unless the caller
            passed ``debug=True``.
    """

    collected: dict[str, tuple[float, list[str]]] = field(default_factory=dict)
    steps: list[Any] = field(default_factory=list)


# ---------------------------------------------------------------------------
# CardPacker (issue #56)
# ---------------------------------------------------------------------------


@runtime_checkable
class CardPacker(Protocol):
    """Pluggable card-rendering stage of the routing pipeline.

    A packer turns a ranked list of items into :class:`ChoiceCard` instances
    within a token budget.  The bundled default
    :class:`~contextweaver.routing.packer.DefaultCardPacker` wraps
    :func:`contextweaver.routing.cards.make_choice_cards`.  Text rendering
    is a separate concern handled by callers (e.g.,
    :func:`~contextweaver.routing.cards.render_cards_text`).
    """

    def pack(
        self,
        items: list[SelectableItem],
        scores: dict[str, float],
        *,
        budget_tokens: int | None = None,
    ) -> list[ChoiceCard]:
        """Render *items* as :class:`ChoiceCard` instances within budget.

        Args:
            items: Ranked items (best first).
            scores: Map of ``item_id`` → score.  Used to populate
                :attr:`ChoiceCard.score`.
            budget_tokens: Optional soft budget.  Implementations may
                truncate the list when the cumulative card token estimate
                would exceed *budget_tokens*; ``None`` disables the cap.

        Returns:
            A list of :class:`ChoiceCard` in ranked order.
        """
        ...


# ---------------------------------------------------------------------------
# EmbeddingBackend (issue #8)
# ---------------------------------------------------------------------------


@runtime_checkable
class EmbeddingBackend(Protocol):
    """Pluggable embedding backend for similarity-based routing.

    Activated by passing an ``embedding_backend`` to
    :class:`~contextweaver.routing.router.Router` (or by registering an
    embedding-backed :class:`Retriever` in the
    :class:`~contextweaver.routing.registry.EngineRegistry`).  The default
    install ships TF-IDF + BM25 only and has no embedding dependency —
    embedding backends arrive via the ``contextweaver[embeddings]`` extra.

    Embedding backends may be non-deterministic across runtime/hardware
    boundaries (e.g. GPU vs CPU sentence-transformers); callers that need
    bit-exact reproducibility should pin the backend's model version and
    embedding cache.  The routing engine's deterministic-by-default
    guarantee applies only when an embedding backend is **not** supplied.
    """

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return a unit-norm-ish embedding for each text in *texts*.

        Implementations should batch internally where possible.  Vectors
        do not need to be exactly L2-normalised — :meth:`similarity` is
        responsible for any normalisation it relies on.
        """
        ...

    def similarity(
        self,
        query_vec: list[float],
        corpus_vecs: list[list[float]],
    ) -> list[float]:
        """Return one similarity score per corpus vector.

        Higher means more similar.  Cosine similarity is the canonical
        choice; implementations may use dot product when vectors are
        already L2-normalised.
        """
        ...
