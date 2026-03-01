"""Protocol definitions and no-op default implementations for contextweaver.

Downstream code should depend on the protocols, not the concrete defaults,
so that stores, hooks, and summarisers remain swappable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from contextweaver.envelope import ContextPack
    from contextweaver.types import ContextItem, SelectableItem


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


@runtime_checkable
class Labeler(Protocol):
    """Assign a category label and confidence score to a SelectableItem."""

    def label(self, item: SelectableItem) -> tuple[str, str]:
        """Return ``(category, confidence)`` for *item*."""
        ...
