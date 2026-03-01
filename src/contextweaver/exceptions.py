"""Exception hierarchy for contextweaver."""

from __future__ import annotations


class ContextWeaverError(Exception):
    """Base exception for all contextweaver errors."""


class BudgetExceededError(ContextWeaverError):
    """Context build cannot fit within the token budget."""


class ArtifactNotFoundError(ContextWeaverError):
    """Artifact handle not found in store."""


class PolicyViolationError(ContextWeaverError):
    """Context operation violates the active policy."""


class ItemNotFoundError(ContextWeaverError):
    """ContextItem ID not found in event log."""


class GraphBuildError(ContextWeaverError):
    """Graph construction or validation failed."""


class RouteError(ContextWeaverError):
    """Routing failed (empty graph, invalid state)."""


class CatalogError(ContextWeaverError):
    """Catalog loading or validation failed."""
