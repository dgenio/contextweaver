"""Protocol definitions and no-op default implementations for contextweaver.

Downstream code should depend on the protocols, not the concrete defaults,
so that stores, hooks, and summarisers remain swappable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from contextweaver.envelope import ContextPack
    from contextweaver.types import ArtifactRef, ContextItem, ItemKind, SelectableItem


# ---------------------------------------------------------------------------
# EventLog
# ---------------------------------------------------------------------------


@runtime_checkable
class EventLog(Protocol):
    """Read/write interface to the ordered event log.

    The event log is the ordered sequence of :class:`~contextweaver.types.ContextItem`
    objects that makes up a conversation / agent session.
    """

    def append(self, item: ContextItem) -> None:
        """Append *item* to the log.

        Raises:
            DuplicateItemError: If an item with the same ``id`` already exists.
        """
        ...

    def get(self, item_id: str) -> ContextItem:
        """Return the item with *item_id*.

        Raises:
            ItemNotFoundError: If no item with *item_id* exists.
        """
        ...

    def all(self) -> list[ContextItem]:
        """Return all items in insertion order."""
        ...

    def filter_by_kind(self, *kinds: ItemKind) -> list[ContextItem]:
        """Return all items whose ``kind`` is in *kinds*."""
        ...

    def tail(self, n: int) -> list[ContextItem]:
        """Return the last *n* items."""
        ...

    def children(self, parent_id: str) -> list[ContextItem]:
        """Return all items whose ``parent_id`` equals *parent_id*."""
        ...

    def parent(self, item_id: str) -> ContextItem | None:
        """Return the parent of *item_id*, or ``None``."""
        ...

    def query(
        self,
        kinds: list[ItemKind] | None = None,
        since: int | None = None,
        limit: int | None = None,
    ) -> list[ContextItem]:
        """Flexible query over the event log."""
        ...

    def count(self) -> int:
        """Return the number of items in the log."""
        ...

    def __len__(self) -> int: ...


# ---------------------------------------------------------------------------
# ArtifactStore
# ---------------------------------------------------------------------------


@runtime_checkable
class ArtifactStore(Protocol):
    """Read/write interface to the out-of-band artifact store.

    Raw tool outputs are stored here; the LLM context pipeline receives only
    :class:`~contextweaver.types.ArtifactRef` handles and summaries.
    """

    def put(
        self,
        handle: str,
        content: bytes,
        media_type: str = "application/octet-stream",
        label: str = "",
    ) -> ArtifactRef:
        """Store *content* and return an :class:`~contextweaver.types.ArtifactRef`."""
        ...

    def get(self, handle: str) -> bytes:
        """Retrieve the raw bytes for *handle*.

        Raises:
            ArtifactNotFoundError: If *handle* is not in the store.
        """
        ...

    def ref(self, handle: str) -> ArtifactRef:
        """Return the :class:`~contextweaver.types.ArtifactRef` metadata for *handle*."""
        ...

    def list_refs(self) -> list[ArtifactRef]:
        """Return all stored :class:`~contextweaver.types.ArtifactRef` objects."""
        ...

    def delete(self, handle: str) -> None:
        """Remove the artifact identified by *handle*."""
        ...

    def exists(self, handle: str) -> bool:
        """Return ``True`` if *handle* is in the store."""
        ...

    def metadata(self, handle: str) -> ArtifactRef:
        """Return the :class:`~contextweaver.types.ArtifactRef` for *handle*."""
        ...

    def drilldown(self, handle: str, selector: dict[str, Any]) -> str:
        """Return a subset of the artifact's content according to *selector*."""
        ...


# FUTURE: EpisodicStore and FactStore protocols — the concrete InMemory*
# classes currently define latest/delete/list_keys without protocol
# declarations.  Add formal protocols once the API surface stabilises.


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


try:
    import tiktoken as _tiktoken

    class TiktokenEstimator:
        """Token estimator backed by OpenAI's ``tiktoken`` library.

        Falls back to :class:`CharDivFourEstimator` if ``tiktoken`` is not
        installed.  *model* may be a model name (e.g. ``"gpt-4"``) or a raw
        encoding name (e.g. ``"cl100k_base"``).  Model names are resolved via
        ``tiktoken.encoding_for_model``; if that fails the value is treated as
        an encoding name.
        """

        def __init__(self, model: str = "cl100k_base") -> None:
            try:
                self._enc = _tiktoken.encoding_for_model(model)
            except KeyError:
                self._enc = _tiktoken.get_encoding(model)

        def estimate(self, text: str) -> int:
            """Return the exact token count using tiktoken."""
            return len(self._enc.encode(text))

except ImportError:  # pragma: no cover

    class TiktokenEstimator:  # type: ignore[no-redef]
        """Stub when ``tiktoken`` is not installed — delegates to :class:`CharDivFourEstimator`.

        *model* is accepted for API compatibility but ignored.
        """

        def __init__(self, model: str = "cl100k_base") -> None:
            _ = model
            self._fallback = CharDivFourEstimator()

        def estimate(self, text: str) -> int:
            """Return ``len(text) // 4`` (tiktoken not available)."""
            return self._fallback.estimate(text)


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
