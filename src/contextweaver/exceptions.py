"""Custom exceptions for contextweaver.

All public-facing errors inherit from :class:`ContextWeaverError` so callers
can catch the whole family with a single ``except`` clause when desired.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from contextweaver.envelope import BuildStats
    from contextweaver.routing.catalog import CatalogValidationReport


class ContextWeaverError(Exception):
    """Base class for all contextweaver errors."""


class BudgetExceededError(ContextWeaverError):
    """Raised when a context build would exceed the configured token budget."""


class BudgetOverflowError(ContextWeaverError):
    """Raised when budget pressure drops candidates under a fail-loud policy.

    Opt-in via :attr:`~contextweaver.config.ContextPolicy.overflow_action`
    (``"raise"``, issue #510): instead of silently dropping items that do not
    fit the token budget, the build raises this so a subtly-wrong prompt
    (e.g. a missing mandatory policy item) surfaces as an immediate,
    debuggable error rather than as bad model output downstream.

    The would-be :class:`~contextweaver.envelope.BuildStats` is attached so
    callers can inspect exactly what was kept, dropped, and why without
    re-running the build.

    Attributes:
        stats: The :class:`~contextweaver.envelope.BuildStats` the build
            produced before raising.
        dropped_kinds: Sorted distinct :class:`~contextweaver.types.ItemKind`
            string values that were dropped for ``"budget"`` and triggered
            the raise.
    """

    def __init__(
        self,
        message: str,
        *,
        stats: BuildStats,
        dropped_kinds: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.stats = stats
        # Normalise to the documented "sorted distinct" form regardless of what
        # the caller passes, so the attribute is consistent.
        self.dropped_kinds = sorted(set(dropped_kinds)) if dropped_kinds else []


class ArtifactNotFoundError(ContextWeaverError):
    """Raised when a requested artifact handle cannot be found in the store."""


class ArtifactStoreQuotaError(ContextWeaverError):
    """Raised when a write would exceed an artifact store's configured quota.

    A persistent :class:`~contextweaver.store.protocols.ArtifactStore` may be
    constructed with ``max_bytes`` / ``max_artifacts`` limits (issue #497);
    a :meth:`put` that would breach either limit raises this instead of
    letting unbounded disk growth go unnoticed in a long-running gateway.
    """


class PolicyViolationError(ContextWeaverError):
    """Raised when an item violates the active :class:`~contextweaver.config.ContextPolicy`."""


class ItemNotFoundError(ContextWeaverError):
    """Raised when a requested item (tool, agent, skill) is not found in the catalog."""


class GraphBuildError(ContextWeaverError):
    """Raised when the routing DAG cannot be constructed (e.g. cycle detected).

    Beyond the human-readable message, validation failures attach structured
    detail so callers can act on the specific offending nodes/edges without
    string-matching the message (issue #523).  The message text is *not* a
    stable API; the structured attributes are:

    Attributes:
        cycle: For cycle failures, the node IDs forming the cycle, including
            the repeated entry/exit node (e.g. ``["a", "b", "c", "a"]``).
            ``None`` for non-cycle failures.
        edge: For dangling-edge failures, the offending ``(src, dst)`` pair.
            ``None`` otherwise.
        missing_root: For missing-root failures, the unresolved root ID.
            ``None`` otherwise.
    """

    def __init__(
        self,
        message: str,
        *,
        cycle: list[str] | None = None,
        edge: tuple[str, str] | None = None,
        missing_root: str | None = None,
    ) -> None:
        super().__init__(message)
        self.cycle = cycle
        self.edge = edge
        self.missing_root = missing_root


class RouteError(ContextWeaverError):
    """Raised when the router cannot produce a valid route through the choice graph."""


class CatalogError(ContextWeaverError):
    """Raised for invalid catalog operations (duplicate IDs, schema violations, etc.)."""


class CatalogValidationError(CatalogError):
    """Raised when a catalog fails cross-item referential validation (issue #519).

    Raised only by the loaders' ``on_invalid="raise"`` path.  The full
    :class:`~contextweaver.routing.catalog.CatalogValidationReport` is attached
    as :attr:`report` so callers can enumerate every dangling reference rather
    than re-running validation after catching the error.

    Attributes:
        report: The populated validation report describing every finding.
    """

    def __init__(self, message: str, *, report: CatalogValidationReport) -> None:
        super().__init__(message)
        self.report = report


class DuplicateItemError(ContextWeaverError):
    """Raised when an item with a duplicate ID is appended to an append-only store."""


class ConfigError(ContextWeaverError):
    """Raised when a configuration value or preset name is invalid."""


class ValidationError(ContextWeaverError, ValueError):
    """Raised when a core data type fails construction-time validation (issue #463).

    Used by the pure-data layer (``envelope.py`` dataclasses such as
    :class:`~contextweaver.envelope.ChoiceCard`, and
    :meth:`~contextweaver.envelope.RoutingDecision.from_dict`) instead of a bare
    ``ValueError``, so the whole error family stays catchable via
    :class:`ContextWeaverError`.  It *also* derives from the builtin
    ``ValueError`` so existing ``except ValueError`` call sites keep working.
    """


class DeterminismError(ContextWeaverError):
    """Raised when a ``deterministic=True`` firewall path would invoke an LLM.

    The context firewall's ``deterministic`` mode (issue #404) *fails closed*:
    rather than silently summarising data through a model, it raises this error
    so regulated/financial/legal callers can guarantee — and prove — that no
    user or account data was passed through a summarisation model.
    """


class PathInvalidError(CatalogError):
    """Raised when a ``tool_browse`` path violates the §3.2 grammar."""


class PathNotFoundError(CatalogError):
    """Raised when a well-formed ``tool_browse`` path resolves to no node."""


class UpstreamError(ContextWeaverError):
    """Raised when an upstream MCP tool call fails for transport/protocol reasons."""


class StoreClosedError(ContextWeaverError):
    """Raised when an operation is attempted on a store whose backing
    resource (e.g. a SQLite connection) has been released via ``close()``.
    """
