"""Protocol definitions and trivial default implementations for contextweaver.

All extension points in the library are defined as protocols here.
Trivial defaults (1-3 lines) also live here; non-trivial defaults live
in their own modules.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from contextweaver.types import ContextItem, Phase, SelectableItem


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
    """Simple heuristic: token count ~ len(text) // 4."""

    def estimate(self, text: str) -> int:
        """Return ``len(text) // 4`` as a rough token estimate."""
        return len(text) // 4


# ---------------------------------------------------------------------------
# EventHook
# ---------------------------------------------------------------------------


@runtime_checkable
class EventHook(Protocol):
    """Lifecycle callbacks fired by the Context Engine."""

    def on_context_built(self, pack: Any, phase: Phase) -> None:
        """Called after a ContextPack is assembled."""
        ...

    def on_firewall_triggered(self, item_id: str, original_size: int, summary_size: int) -> None:
        """Called when a raw tool output is intercepted by the context firewall."""
        ...

    def on_items_excluded(self, excluded: list[tuple[str, str]]) -> None:
        """Called when items are dropped from the context."""
        ...

    def on_budget_exceeded(self, requested: int, available: int) -> None:
        """Called when a build exceeds the configured token budget."""
        ...

    def on_route_completed(self, query: str, candidates: list[str], depth: int) -> None:
        """Called after the router produces a route through the choice graph."""
        ...


class NoOpHook:
    """Default no-op implementation of EventHook."""

    def on_context_built(self, pack: Any, phase: Any) -> None:
        """No-op."""

    def on_firewall_triggered(self, item_id: str, original_size: int, summary_size: int) -> None:
        """No-op."""

    def on_items_excluded(self, excluded: list[tuple[str, str]]) -> None:
        """No-op."""

    def on_budget_exceeded(self, requested: int, available: int) -> None:
        """No-op."""

    def on_route_completed(self, query: str, candidates: list[str], depth: int) -> None:
        """No-op."""


# ---------------------------------------------------------------------------
# Summarizer / Extractor
# ---------------------------------------------------------------------------


@runtime_checkable
class Summarizer(Protocol):
    """Convert a raw tool output string into a human/LLM-readable summary."""

    def summarize(self, text: str, max_chars: int = 300) -> str:
        """Return a summary of *text*."""
        ...


@runtime_checkable
class Extractor(Protocol):
    """Extract structured facts from a raw tool output."""

    def extract(self, text: str, media_type: str = "text/plain") -> dict[str, Any]:
        """Return structured extraction from *text*."""
        ...


# ---------------------------------------------------------------------------
# RedactionHook
# ---------------------------------------------------------------------------


@runtime_checkable
class RedactionHook(Protocol):
    """Apply redaction rules to a ContextItem."""

    def redact(self, item: ContextItem) -> ContextItem | None:
        """Return a (possibly modified) copy of *item*, or None to drop."""
        ...


# ---------------------------------------------------------------------------
# Labeler
# ---------------------------------------------------------------------------


@runtime_checkable
class Labeler(Protocol):
    """Assign a category label to a group of SelectableItems."""

    def label(self, items: list[SelectableItem]) -> tuple[str, str]:
        """Return (label, routing_hint) for a group of items."""
        ...
