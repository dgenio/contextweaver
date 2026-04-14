"""Custom exceptions for contextweaver.

All public-facing errors inherit from :class:`ContextWeaverError` so callers
can catch the whole family with a single ``except`` clause when desired.
"""

from __future__ import annotations


class ContextWeaverError(Exception):
    """Base class for all contextweaver errors."""


class BudgetExceededError(ContextWeaverError):
    """Raised when a context build would exceed the configured token budget."""


class ArtifactNotFoundError(ContextWeaverError):
    """Raised when a requested artifact handle cannot be found in the store."""


class PolicyViolationError(ContextWeaverError):
    """Raised when an item violates the active :class:`~contextweaver.config.ContextPolicy`."""


class ItemNotFoundError(ContextWeaverError):
    """Raised when a requested item (tool, agent, skill) is not found in the catalog."""


class GraphBuildError(ContextWeaverError):
    """Raised when the routing DAG cannot be constructed (e.g. cycle detected)."""


class RouteError(ContextWeaverError):
    """Raised when the router cannot produce a valid route through the choice graph."""


class CatalogError(ContextWeaverError):
    """Raised for invalid catalog operations (duplicate IDs, schema violations, etc.)."""


class DuplicateItemError(ContextWeaverError):
    """Raised when an item with a duplicate ID is appended to an append-only store."""
