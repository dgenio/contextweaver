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
    """Estimate the number of tokens in a text string.

    The Context Engine uses a TokenEstimator to compute token counts for
    candidate items during the selection phase. Items without an explicit
    ``token_estimate`` rely on the estimator to determine their size.

    Two built-in implementations are provided:

    * :class:`CharDivFourEstimator` — fast heuristic, no external deps
    * :class:`TiktokenEstimator` — exact counts via OpenAI's tiktoken

    Usage::

        from contextweaver.protocols import CharDivFourEstimator, TiktokenEstimator

        # Simple heuristic (default)
        estimator = CharDivFourEstimator()
        count = estimator.estimate("Hello, world!")  # Returns 3

        # Exact tokenization
        estimator = TiktokenEstimator(model="gpt-4")
        count = estimator.estimate("Hello, world!")  # Returns 4

    See Also:
        :func:`~contextweaver.context.selection.select_and_pack` — uses the
        estimator for items lacking a pre-computed token count.
    """

    def estimate(self, text: str) -> int:
        """Return the estimated token count for *text*."""
        ...


class CharDivFourEstimator:
    """Simple heuristic: token count ≈ len(text) // 4.

    This is the default estimator used by :class:`~contextweaver.context.manager.ContextManager`
    when no explicit estimator is provided. It requires no external dependencies
    and works offline.

    The heuristic assumes an average token length of 4 characters, which is
    roughly accurate for English text with tiktoken encodings (cl100k_base).
    For non-English text or code, the estimate may be less accurate.

    Example::

        >>> estimator = CharDivFourEstimator()
        >>> estimator.estimate("Hello, world!")
        3
        >>> estimator.estimate("x" * 100)
        25

    See Also:
        :class:`TiktokenEstimator` — for exact token counts when tiktoken is available.
    """

    def estimate(self, text: str) -> int:
        """Return ``len(text) // 4`` as a rough token estimate."""
        return len(text) // 4


class TiktokenEstimator:
    """Token estimator backed by OpenAI's ``tiktoken`` library.

    Provides exact token counts using the same BPE encodings as OpenAI models.
    Falls back to :class:`CharDivFourEstimator` if the encoding cannot be loaded
    (e.g., in offline/air-gapped environments).

    Args:
        model: Model name (e.g., ``"gpt-4"``) or raw encoding name
            (e.g., ``"cl100k_base"``). Model names are resolved via
            ``tiktoken.encoding_for_model``; if that fails, the value is treated
            as an encoding name.

    Attributes:
        _enc: The tiktoken Encoding instance (or None if fallback is active).
        _fallback: CharDivFourEstimator instance used when tiktoken fails.

    Example::

        >>> estimator = TiktokenEstimator(model="gpt-4")
        >>> estimator.estimate("Hello, world!")
        4

    Note:
        tiktoken downloads BPE encoding files on first use. In offline environments,
        set ``TIKTOKEN_CACHE_DIR`` to a pre-populated cache directory, or the
        estimator will transparently fall back to CharDivFourEstimator.

    See Also:
        https://github.com/openai/tiktoken — tiktoken documentation
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


# LLM-backed implementations live in ``contextweaver.extras.llm_summarizer``
# (``LlmSummarizer`` / ``LlmExtractor``); see issue #26.


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
                :attr:
