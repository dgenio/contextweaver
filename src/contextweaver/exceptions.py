"""Custom exceptions for contextweaver.

All public-facing errors inherit from :class:`ContextWeaverError` so callers
can catch the whole family with a single ``except`` clause when desired.

Every exception class also carries a stable, machine-readable
:attr:`~ContextWeaverError.code` (e.g. ``"CW_CONFIG"``) so programs can branch
on failures without string-matching the message, plus an optional one-line
:attr:`~ContextWeaverError.hint` pointing at the remediation in the error
reference (``docs/errors.md``).  Codes are part of the public compatibility
surface: ``tests/test_exceptions.py`` freezes them against a golden list so a
rename or a missing code fails CI (issue #635).  The human-readable causes and
fixes for every code live on the error-reference page (issue #637).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from contextweaver.adapters.startup_policy import StartupReport
    from contextweaver.envelope import BuildStats
    from contextweaver.routing.catalog import CatalogValidationReport

#: Base URL of the published error-reference page (issue #637).  ``default_hint``
#: values anchor here; section anchors are the lower-cased class name.
_ERRORS_DOC = "https://dgenio.github.io/contextweaver/errors"


class ContextWeaverError(Exception):
    """Base class for all contextweaver errors.

    Attributes:
        code: Stable, machine-readable identifier for the error class (e.g.
            ``"CW_ERROR"``).  Class-level and frozen — safe to branch on and to
            log, alert on, or translate across the CLI, the gateway boundary, and
            non-Python clients without parsing the message text.
        hint: Optional one-line remediation pointer (usually a link into the
            error reference).  Falls back to the class-level :attr:`default_hint`
            when the caller does not pass one; ``None`` when neither is set.
    """

    code: ClassVar[str] = "CW_ERROR"
    default_hint: ClassVar[str | None] = None

    def __init__(self, *args: object, hint: str | None = None) -> None:
        super().__init__(*args)
        self.hint: str | None = hint if hint is not None else self.default_hint

    def __str__(self) -> str:
        message = super().__str__()
        rendered = f"[{self.code}] {message}" if message else f"[{self.code}]"
        if self.hint:
            rendered = f"{rendered} (hint: {self.hint})"
        return rendered


class BudgetExceededError(ContextWeaverError):
    """Raised when a context build would exceed the configured token budget."""

    code: ClassVar[str] = "CW_BUDGET_EXCEEDED"


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

    code: ClassVar[str] = "CW_BUDGET_OVERFLOW"
    default_hint: ClassVar[str | None] = (
        f"raise the phase token budget or set overflow_action='drop'; "
        f"see {_ERRORS_DOC}/#budgetoverflowerror"
    )

    def __init__(
        self,
        message: str,
        *,
        stats: BuildStats,
        dropped_kinds: list[str] | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message, hint=hint)
        self.stats = stats
        # Normalise to the documented "sorted distinct" form regardless of what
        # the caller passes, so the attribute is consistent.
        self.dropped_kinds = sorted(set(dropped_kinds)) if dropped_kinds else []


class ArtifactNotFoundError(ContextWeaverError):
    """Raised when a requested artifact handle cannot be found in the store."""

    code: ClassVar[str] = "CW_ARTIFACT_NOT_FOUND"


class ArtifactStoreQuotaError(ContextWeaverError):
    """Raised when a write would exceed an artifact store's configured quota.

    A persistent :class:`~contextweaver.store.protocols.ArtifactStore` may be
    constructed with ``max_bytes`` / ``max_artifacts`` limits (issue #497);
    a :meth:`put` that would breach either limit raises this instead of
    letting unbounded disk growth go unnoticed in a long-running gateway.
    """

    code: ClassVar[str] = "CW_ARTIFACT_STORE_QUOTA"


class PolicyViolationError(ContextWeaverError):
    """Raised when an item violates the active :class:`~contextweaver.config.ContextPolicy`."""

    code: ClassVar[str] = "CW_POLICY_VIOLATION"


class ItemNotFoundError(ContextWeaverError):
    """Raised when a requested item (tool, agent, skill) is not found in the catalog."""

    code: ClassVar[str] = "CW_ITEM_NOT_FOUND"
    default_hint: ClassVar[str | None] = (
        f"check the tool/agent/skill ID exists in the catalog; see {_ERRORS_DOC}/#itemnotfounderror"
    )


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

    code: ClassVar[str] = "CW_GRAPH_BUILD"

    def __init__(
        self,
        message: str,
        *,
        cycle: list[str] | None = None,
        edge: tuple[str, str] | None = None,
        missing_root: str | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message, hint=hint)
        self.cycle = cycle
        self.edge = edge
        self.missing_root = missing_root


class RouteError(ContextWeaverError):
    """Raised when the router cannot produce a valid route through the choice graph."""

    code: ClassVar[str] = "CW_ROUTE"


class CatalogError(ContextWeaverError):
    """Raised for invalid catalog operations (duplicate IDs, schema violations, etc.)."""

    code: ClassVar[str] = "CW_CATALOG"
    default_hint: ClassVar[str | None] = (
        f"validate the catalog for duplicate IDs / schema violations; "
        f"see {_ERRORS_DOC}/#catalogerror"
    )


class CatalogValidationError(CatalogError):
    """Raised when a catalog fails cross-item referential validation (issue #519).

    Raised only by the loaders' ``on_invalid="raise"`` path.  The full
    :class:`~contextweaver.routing.catalog.CatalogValidationReport` is attached
    as :attr:`report` so callers can enumerate every dangling reference rather
    than re-running validation after catching the error.

    Attributes:
        report: The populated validation report describing every finding.
    """

    code: ClassVar[str] = "CW_CATALOG_VALIDATION"

    def __init__(
        self,
        message: str,
        *,
        report: CatalogValidationReport,
        hint: str | None = None,
    ) -> None:
        super().__init__(message, hint=hint)
        self.report = report


class DuplicateItemError(ContextWeaverError):
    """Raised when an item with a duplicate ID is appended to an append-only store."""

    code: ClassVar[str] = "CW_DUPLICATE_ITEM"


class ConfigError(ContextWeaverError):
    """Raised when a configuration value or preset name is invalid."""

    code: ClassVar[str] = "CW_CONFIG"
    default_hint: ClassVar[str | None] = (
        f"check the configuration value or preset name; see {_ERRORS_DOC}/#configerror"
    )


class ValidationError(ContextWeaverError, ValueError):
    """Raised when a core data type fails construction-time validation (issue #463).

    Used by the pure-data layer (``envelope.py`` dataclasses such as
    :class:`~contextweaver.envelope.ChoiceCard`, and
    :meth:`~contextweaver.envelope.RoutingDecision.from_dict`) instead of a bare
    ``ValueError``, so the whole error family stays catchable via
    :class:`ContextWeaverError`.  It *also* derives from the builtin
    ``ValueError`` so existing ``except ValueError`` call sites keep working.
    """

    code: ClassVar[str] = "CW_VALIDATION"


class DeterminismError(ContextWeaverError):
    """Raised when a ``deterministic=True`` firewall path would invoke an LLM.

    The context firewall's ``deterministic`` mode (issue #404) *fails closed*:
    rather than silently summarising data through a model, it raises this error
    so regulated/financial/legal callers can guarantee — and prove — that no
    user or account data was passed through a summarisation model.
    """

    code: ClassVar[str] = "CW_DETERMINISM"
    default_hint: ClassVar[str | None] = (
        f"provide a deterministic (rule-based) summarizer/extractor, or disable "
        f"deterministic mode if model calls are acceptable; "
        f"see {_ERRORS_DOC}/#determinismerror"
    )


class PathInvalidError(CatalogError):
    """Raised when a ``tool_browse`` path violates the §3.2 grammar."""

    code: ClassVar[str] = "CW_PATH_INVALID"
    default_hint: ClassVar[str | None] = (
        f"fix the tool_browse path against the §3.2 grammar; see {_ERRORS_DOC}/#pathinvaliderror"
    )


class PathNotFoundError(CatalogError):
    """Raised when a well-formed ``tool_browse`` path resolves to no node."""

    code: ClassVar[str] = "CW_PATH_NOT_FOUND"
    default_hint: ClassVar[str | None] = (
        f"browse from the root to discover valid paths; the catalog may have "
        f"changed since the path was built. See {_ERRORS_DOC}/#pathnotfounderror"
    )


class UpstreamError(ContextWeaverError):
    """Raised when an upstream MCP tool call fails for transport/protocol reasons."""

    code: ClassVar[str] = "CW_UPSTREAM"


class StoreClosedError(ContextWeaverError):
    """Raised when an operation is attempted on a store whose backing
    resource (e.g. a SQLite connection) has been released via ``close()``.
    """

    code: ClassVar[str] = "CW_STORE_CLOSED"


class UpstreamStartupError(ContextWeaverError):
    """Raised when live multi-upstream startup fails under the configured
    ``StartupPolicy`` (issue #374): a required upstream failed under
    ``mode="strict"``, too few upstreams started, or the catalog is empty.

    Attributes:
        report: The ``StartupReport`` describing every upstream's outcome.
    """

    code: ClassVar[str] = "CW_UPSTREAM_STARTUP"
    default_hint: ClassVar[str | None] = (
        f"inspect the attached report, or relax startup.mode/min_healthy_upstreams; "
        f"see {_ERRORS_DOC}/#upstreamstartuperror"
    )

    def __init__(
        self, message: str, *, report: StartupReport | None = None, hint: str | None = None
    ) -> None:
        super().__init__(message, hint=hint)
        self.report = report
