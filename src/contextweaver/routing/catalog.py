"""Tool catalog management for the contextweaver Routing Engine.

The :class:`Catalog` holds all registered :class:`~contextweaver.types.SelectableItem`
objects and provides lookup, filtering, and namespace-scoped views.

Convenience loaders:

- :func:`load_catalog_json` — read items from a JSON file on disk.
- :func:`load_catalog_dicts` — convert raw dicts (e.g. from an API) to items.
- :func:`generate_sample_catalog` — deterministic sample data factory for demos.
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from contextweaver.envelope import HydrationResult
from contextweaver.exceptions import CatalogError, CatalogValidationError, ItemNotFoundError
from contextweaver.types import SelectableItem

logger = logging.getLogger("contextweaver.routing")

#: Policy for how loaders react to broken cross-item references (issue #519).
OnInvalid = Literal["warn", "raise", "ignore"]


@dataclass(frozen=True)
class ReferenceFinding:
    """A single broken cross-item reference found during validation (issue #519).

    Attributes:
        item_id: ID of the item carrying the dangling reference.
        field: The referencing field — ``"depends_on"`` (references another
            item ID) or ``"requires"`` (references a capability that some
            item must ``provides``).
        missing: The unresolved target — an item ID for ``depends_on`` or a
            capability string for ``requires``.
    """

    item_id: str
    field: Literal["depends_on", "requires"]
    missing: str

    def message(self) -> str:
        """Return a human-readable one-line description of the finding."""
        if self.field == "depends_on":
            return f"Item {self.item_id!r} depends_on unknown tool id {self.missing!r}"
        return (
            f"Item {self.item_id!r} requires capability {self.missing!r} not provided by any item"
        )

    def to_dict(self) -> dict[str, str]:
        """Serialise to a JSON-compatible dict."""
        return {"item_id": self.item_id, "field": self.field, "missing": self.missing}


@dataclass
class CatalogValidationReport:
    """Typed result of cross-item referential validation (issue #519).

    Reports dangling ``depends_on`` (item-ID) references and unsatisfied
    ``requires`` (capability) references in deterministic, sorted order.
    An empty :attr:`findings` list means every reference closes within the
    catalog.

    Attributes:
        items_processed: Total items inspected.
        findings: Sorted list of :class:`ReferenceFinding` entries.
    """

    items_processed: int = 0
    findings: list[ReferenceFinding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """``True`` when no broken references were found."""
        return not self.findings

    def messages(self) -> list[str]:
        """Return one human-readable warning string per finding (sorted)."""
        return [f.message() for f in self.findings]

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "items_processed": self.items_processed,
            "findings": [f.to_dict() for f in self.findings],
        }


def validate_references(items: list[SelectableItem]) -> CatalogValidationReport:
    """Validate cross-item references across *items* (issue #519).

    Two reference kinds are checked, deterministically and without mutating
    or dropping any item:

    * ``depends_on`` — every referenced ID must exist as an item ID.
    * ``requires`` — every required capability must be declared in some
      item's ``provides`` list.

    Args:
        items: The catalog items to validate.

    Returns:
        A :class:`CatalogValidationReport` whose ``findings`` are sorted by
        ``(item_id, field, missing)`` for reproducible output.
    """
    known_ids = {item.id for item in items}
    provided: set[str] = set()
    for item in items:
        if item.provides:
            provided.update(item.provides)

    findings: list[ReferenceFinding] = []
    for item in items:
        for dep in item.depends_on or ():
            if dep not in known_ids:
                findings.append(ReferenceFinding(item.id, "depends_on", dep))
        for cap in item.requires or ():
            if cap not in provided:
                findings.append(ReferenceFinding(item.id, "requires", cap))

    findings.sort(key=lambda f: (f.item_id, f.field, f.missing))
    return CatalogValidationReport(items_processed=len(items), findings=findings)


def _apply_reference_policy(items: list[SelectableItem], on_invalid: OnInvalid) -> None:
    """Run referential validation and react per *on_invalid* (issue #519).

    ``"warn"`` logs one WARNING per finding and returns; ``"raise"`` raises
    :class:`~contextweaver.exceptions.CatalogValidationError` carrying the
    report; ``"ignore"`` skips validation entirely.
    """
    if on_invalid == "ignore":
        return
    report = validate_references(items)
    if report.ok:
        return
    if on_invalid == "raise":
        raise CatalogValidationError(
            f"Catalog has {len(report.findings)} broken reference(s): "
            f"{'; '.join(report.messages())}",
            report=report,
        )
    for finding in report.findings:
        logger.warning("load_catalog: %s", finding.message())


class Catalog:
    """Registry of :class:`~contextweaver.types.SelectableItem` objects.

    All item IDs must be unique within a catalog.  Namespace filtering and
    tag-based queries are supported.
    """

    def __init__(self) -> None:
        self._items: dict[str, SelectableItem] = {}

    def register(self, item: SelectableItem) -> None:
        """Add *item* to the catalog.

        Args:
            item: The item to register.

        Raises:
            CatalogError: If an item with the same ``id`` is already registered.
        """
        if item.id in self._items:
            raise CatalogError(f"Duplicate item id: {item.id!r}")
        self._items[item.id] = item

    def get(self, item_id: str) -> SelectableItem:
        """Return the item with *item_id*.

        Args:
            item_id: Unique identifier.

        Returns:
            The matching :class:`~contextweaver.types.SelectableItem`.

        Raises:
            ItemNotFoundError: If no item with *item_id* exists.
        """
        if item_id not in self._items:
            raise ItemNotFoundError(f"Item not found: {item_id!r}")
        return self._items[item_id]

    def all(self) -> list[SelectableItem]:
        """Return all items sorted by id.

        Returns:
            A list of all registered items.
        """
        return [self._items[k] for k in sorted(self._items)]

    def filter_by_namespace(self, namespace: str) -> list[SelectableItem]:
        """Return items whose ``namespace`` matches *namespace*.

        Args:
            namespace: Exact namespace string to filter on.

        Returns:
            A list of matching items sorted by id.
        """
        return sorted(
            (item for item in self._items.values() if item.namespace == namespace),
            key=lambda i: i.id,
        )

    def filter_by_tags(self, *tags: str) -> list[SelectableItem]:
        """Return items that have **all** of the specified *tags*.

        Args:
            *tags: Tag strings that must all be present on the item.

        Returns:
            A list of matching items sorted by id.
        """
        tag_set = set(tags)
        return sorted(
            (item for item in self._items.values() if tag_set.issubset(item.tags)),
            key=lambda i: i.id,
        )

    def validate_dependencies(self) -> list[str]:
        """Return human-readable warnings about ``depends_on`` references (issue #27).

        Returns one warning per item whose ``depends_on`` references a tool
        id that is not registered in this catalog.  An empty list means the
        dependency graph closes within the catalog.

        Returns:
            Sorted list of warning strings.  Empty when no issues found.
        """
        warnings: list[str] = []
        known = set(self._items)
        for item_id in sorted(self._items):
            item = self._items[item_id]
            if not item.depends_on:
                continue
            for dep in item.depends_on:
                if dep not in known:
                    warnings.append(f"Item {item_id!r} depends_on unknown tool id {dep!r}")
        return warnings

    def validate_references(self) -> CatalogValidationReport:
        """Return a typed report of broken cross-item references (issue #519).

        Unlike :meth:`validate_dependencies` (which returns only ``depends_on``
        warning strings for backward compatibility), this validates both
        ``depends_on`` (item IDs) and ``requires`` (capabilities satisfied by
        some item's ``provides``) and returns a structured
        :class:`CatalogValidationReport` suitable for the ``catalog lint`` CLI.

        Returns:
            A :class:`CatalogValidationReport`; ``report.ok`` is ``True`` when
            every reference resolves within the catalog.
        """
        return validate_references(self.all())

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {"items": [item.to_dict() for item in self.all()]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Catalog:
        """Deserialise from a JSON-compatible dict produced by :meth:`to_dict`."""
        catalog = cls()
        for raw in data.get("items", []):
            catalog.register(SelectableItem.from_dict(raw))
        return catalog

    def hydrate(self, tool_id: str) -> HydrationResult:
        """Return the full schema, constraints, examples, and cost hints for a tool.

        Intended to be called after routing to assemble a ``Phase.call`` prompt
        with the selected tool's complete argument schema.

        Args:
            tool_id: Unique identifier of the tool to hydrate.

        Returns:
            A :class:`~contextweaver.envelope.HydrationResult` containing the
            full item and its schema details.  The returned ``args_schema``,
            ``examples``, and ``constraints`` are **shallow copies** — nested
            values are shared with the catalog item.  Callers should treat
            them as read-only; use :func:`copy.deepcopy` if mutation is needed.

        Raises:
            ItemNotFoundError: If *tool_id* is not registered.
        """
        item = self.get(tool_id)
        return HydrationResult(
            item=item,
            args_schema=dict(item.args_schema),
            examples=list(item.examples),
            constraints=dict(item.constraints),
        )


# ---------------------------------------------------------------------------
# Convenience loaders
# ---------------------------------------------------------------------------


def load_catalog_json(path: str | Path, *, on_invalid: OnInvalid = "warn") -> list[SelectableItem]:
    """Load :class:`SelectableItem` objects from a JSON file.

    The file must contain a JSON array of item dicts, each with at least
    ``id``, ``kind``, ``name``, and ``description`` fields.

    Args:
        path: Filesystem path to a JSON file.
        on_invalid: How to react to broken cross-item references (issue
            #519): ``"warn"`` (default) logs each dangling reference and
            returns; ``"raise"`` raises
            :class:`~contextweaver.exceptions.CatalogValidationError` with the
            report attached; ``"ignore"`` skips referential validation.

    Returns:
        A list of :class:`SelectableItem` objects.

    Raises:
        CatalogError: If the file cannot be read or parsed, or if any item
            dict is missing required fields.
        CatalogValidationError: If *on_invalid* is ``"raise"`` and the catalog
            contains a broken cross-item reference.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise CatalogError(f"Cannot read catalog file: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CatalogError(f"Invalid JSON in catalog file: {exc}") from exc
    if not isinstance(data, list):
        raise CatalogError("Catalog JSON must be an array of item dicts.")
    return load_catalog_dicts(data, on_invalid=on_invalid)


def load_catalog_yaml(path: str | Path, *, on_invalid: OnInvalid = "warn") -> list[SelectableItem]:
    """Load :class:`SelectableItem` objects from a YAML file.

    The file must contain a YAML sequence of item mappings, each with at
    least ``id``, ``kind``, ``name``, and ``description`` keys. Equivalent
    to :func:`load_catalog_json` but using YAML syntax for human-friendly
    catalog authoring.

    Args:
        path: Filesystem path to a YAML file.
        on_invalid: Reference-validation policy; see :func:`load_catalog_json`.

    Returns:
        A list of :class:`SelectableItem` objects.

    Raises:
        CatalogError: If the file cannot be read or parsed, or if any item
            mapping is missing required fields.
        CatalogValidationError: If *on_invalid* is ``"raise"`` and the catalog
            contains a broken cross-item reference.
    """
    import yaml  # core dep — see pyproject.toml

    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise CatalogError(f"Cannot read catalog file: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CatalogError(f"Invalid YAML in catalog file: {exc}") from exc
    if not isinstance(data, list):
        raise CatalogError("Catalog YAML must be a sequence of item mappings.")
    return load_catalog_dicts(data, on_invalid=on_invalid)


def load_catalog(path: str | Path, *, on_invalid: OnInvalid = "warn") -> list[SelectableItem]:
    """Load a catalog file, auto-detecting format from the file extension.

    ``.yaml`` / ``.yml`` extensions dispatch to :func:`load_catalog_yaml`;
    everything else is treated as JSON via :func:`load_catalog_json`.

    Args:
        path: Filesystem path to a catalog file.
        on_invalid: Reference-validation policy; see :func:`load_catalog_json`.

    Returns:
        A list of :class:`SelectableItem` objects.
    """
    suffix = Path(path).suffix.lower()
    if suffix in (".yaml", ".yml"):
        return load_catalog_yaml(path, on_invalid=on_invalid)
    return load_catalog_json(path, on_invalid=on_invalid)


def load_catalog_dicts(
    data: list[dict[str, Any]], *, on_invalid: OnInvalid = "warn"
) -> list[SelectableItem]:
    """Convert a list of raw dicts into :class:`SelectableItem` objects.

    Each dict must have at least ``id``, ``kind``, ``name``, and
    ``description`` keys.  After successful deserialization, cross-item
    references are validated according to *on_invalid* (issue #519).

    Args:
        data: A list of JSON-compatible dicts.
        on_invalid: Reference-validation policy; see :func:`load_catalog_json`.

    Returns:
        A list of :class:`SelectableItem` objects.

    Raises:
        CatalogError: If any dict is missing required fields or has invalid
            values.  Per-item failures name the offending item by ``id`` (or
            index when the id is absent) to locate the bug in large catalogs.
        CatalogValidationError: If *on_invalid* is ``"raise"`` and the catalog
            contains a broken cross-item reference.
    """
    required = {"id", "kind", "name", "description"}
    items: list[SelectableItem] = []
    for i, raw in enumerate(data):
        if not isinstance(raw, dict):
            raise CatalogError(f"Item at index {i} is not a dict.")
        missing = required - set(raw)
        if missing:
            where = _item_location(raw, i)
            raise CatalogError(f"Item {where} missing required fields: {sorted(missing)}")
        try:
            items.append(SelectableItem.from_dict(raw))
        except (KeyError, TypeError, ValueError) as exc:
            where = _item_location(raw, i)
            raise CatalogError(f"Item {where} has invalid data: {exc}") from exc
    _apply_reference_policy(items, on_invalid)
    return items


def _item_location(raw: dict[str, Any], index: int) -> str:
    """Return an ``id=...`` (or ``at index N``) locator for error messages."""
    item_id = raw.get("id")
    if isinstance(item_id, str) and item_id:
        return f"{item_id!r} (index {index})"
    return f"at index {index}"


# ---------------------------------------------------------------------------
# Sample catalog generator
# ---------------------------------------------------------------------------

_SAMPLE_FAMILIES: dict[str, list[tuple[str, str, list[str]]]] = {
    "billing": [
        ("invoices.create", "Create a new invoice", ["billing", "create"]),
        ("invoices.search", "Search invoices by date or customer", ["billing", "search"]),
        ("invoices.get", "Retrieve a specific invoice by ID", ["billing", "read"]),
        ("invoices.void", "Void an unpaid invoice", ["billing", "write"]),
        ("payments.charge", "Charge a stored payment method", ["billing", "payments"]),
        ("payments.refund", "Refund a completed payment", ["billing", "payments"]),
        ("payments.list", "List payment history", ["billing", "payments", "search"]),
        ("reports.revenue", "Generate revenue summary report", ["billing", "reports"]),
        ("reports.aging", "Generate accounts-receivable aging report", ["billing", "reports"]),
        ("reports.forecast", "Generate revenue forecast", ["billing", "reports"]),
        ("subscriptions.create", "Create a subscription plan", ["billing", "subscriptions"]),
        ("subscriptions.cancel", "Cancel an active subscription", ["billing", "subscriptions"]),
        ("subscriptions.update", "Update subscription details", ["billing", "subscriptions"]),
        ("subscriptions.list", "List active subscriptions", ["billing", "subscriptions", "search"]),
    ],
    "crm": [
        ("contacts.create", "Create a new contact record", ["crm", "contacts", "create"]),
        ("contacts.find", "Find contacts by name or email", ["crm", "contacts", "search"]),
        ("contacts.update", "Update contact fields", ["crm", "contacts", "write"]),
        ("contacts.merge", "Merge duplicate contacts", ["crm", "contacts"]),
        ("deals.create", "Create a new deal/opportunity", ["crm", "deals", "create"]),
        ("deals.search", "Search deals by stage or value", ["crm", "deals", "search"]),
        ("deals.close", "Mark a deal as won or lost", ["crm", "deals", "write"]),
        ("deals.pipeline", "View the deal pipeline summary", ["crm", "deals"]),
        ("activities.log", "Log a customer activity", ["crm", "activities", "create"]),
        (
            "activities.list",
            "List recent activities for a contact",
            ["crm", "activities", "search"],
        ),
        ("activities.delete", "Delete a logged activity", ["crm", "activities", "write"]),
        ("activities.stats", "Get activity statistics", ["crm", "activities", "reports"]),
    ],
    "search": [
        ("documents.index", "Index a document for full-text search", ["search", "documents"]),
        ("documents.query", "Full-text search across documents", ["search", "documents", "query"]),
        ("documents.delete", "Remove a document from the index", ["search", "documents"]),
        ("web.search", "Search the public web", ["search", "web"]),
        ("web.scrape", "Scrape a web page and extract text", ["search", "web"]),
        ("web.summarize", "Summarize a web page", ["search", "web"]),
        ("internal.search", "Search internal knowledge base", ["search", "internal"]),
        ("internal.suggest", "Suggest related internal documents", ["search", "internal"]),
        ("internal.reindex", "Reindex the internal knowledge base", ["search", "internal"]),
    ],
    "docs": [
        ("pages.create", "Create a new documentation page", ["docs", "pages", "create"]),
        ("pages.update", "Update an existing page", ["docs", "pages", "write"]),
        ("pages.publish", "Publish a draft page", ["docs", "pages"]),
        ("pages.archive", "Archive an old page", ["docs", "pages"]),
        ("pages.search", "Search documentation pages", ["docs", "pages", "search"]),
        ("templates.create", "Create a new page template", ["docs", "templates", "create"]),
        ("templates.list", "List available templates", ["docs", "templates", "search"]),
        ("templates.apply", "Apply a template to a page", ["docs", "templates"]),
        ("templates.delete", "Delete a page template", ["docs", "templates", "write"]),
    ],
    "admin": [
        ("users.create", "Create a new user account", ["admin", "users", "create"]),
        ("users.deactivate", "Deactivate a user account", ["admin", "users", "write"]),
        ("users.list", "List all user accounts", ["admin", "users", "search"]),
        ("users.reset_password", "Reset a user password", ["admin", "users"]),
        ("roles.create", "Create a custom role", ["admin", "roles", "create"]),
        ("roles.assign", "Assign a role to a user", ["admin", "roles", "write"]),
        ("roles.list", "List available roles", ["admin", "roles", "search"]),
        ("audit.query", "Query the audit log", ["admin", "audit", "search"]),
        ("audit.export", "Export audit log entries", ["admin", "audit"]),
        ("audit.retention", "Configure audit log retention", ["admin", "audit"]),
    ],
    "comms": [
        ("email.send", "Send an email message", ["comms", "email", "send"]),
        ("email.draft", "Create an email draft", ["comms", "email", "create"]),
        ("email.list", "List emails in a mailbox", ["comms", "email", "search"]),
        ("email.template", "Render an email template", ["comms", "email"]),
        ("slack.post", "Post a message to a Slack channel", ["comms", "slack", "send"]),
        ("slack.search", "Search Slack message history", ["comms", "slack", "search"]),
        ("slack.react", "Add a reaction to a Slack message", ["comms", "slack"]),
        ("notifications.send", "Send a push notification", ["comms", "notifications", "send"]),
        ("notifications.subscribe", "Subscribe to notification events", ["comms", "notifications"]),
        ("notifications.list", "List notification history", ["comms", "notifications", "search"]),
    ],
    "analytics": [
        ("events.track", "Track a custom analytics event", ["analytics", "events", "create"]),
        ("events.query", "Query analytics events by date range", ["analytics", "events", "search"]),
        ("events.export", "Export analytics events to CSV", ["analytics", "events"]),
        (
            "dashboards.create",
            "Create an analytics dashboard",
            ["analytics", "dashboards", "create"],
        ),
        ("dashboards.list", "List available dashboards", ["analytics", "dashboards", "search"]),
        ("dashboards.share", "Share a dashboard with a team", ["analytics", "dashboards"]),
        ("metrics.define", "Define a custom metric", ["analytics", "metrics", "create"]),
        ("metrics.query", "Query metric time-series data", ["analytics", "metrics", "search"]),
        ("metrics.alert", "Set up an alert on a metric threshold", ["analytics", "metrics"]),
    ],
    "infra": [
        ("deployments.create", "Create a new deployment", ["infra", "deployments", "create"]),
        ("deployments.rollback", "Rollback to a previous deployment", ["infra", "deployments"]),
        ("deployments.status", "Check deployment status", ["infra", "deployments", "read"]),
        ("deployments.list", "List recent deployments", ["infra", "deployments", "search"]),
        ("secrets.set", "Store a secret value", ["infra", "secrets", "write"]),
        ("secrets.get", "Retrieve a secret value", ["infra", "secrets", "read"]),
        ("secrets.list", "List available secrets", ["infra", "secrets", "search"]),
        ("secrets.rotate", "Rotate a secret", ["infra", "secrets"]),
        ("logs.query", "Query application logs", ["infra", "logs", "search"]),
        ("logs.tail", "Tail live application logs", ["infra", "logs"]),
    ],
}


def generate_sample_catalog(n: int = 80, seed: int = 42) -> list[dict[str, Any]]:
    """Generate a deterministic sample catalog of *n* items.

    Items are drawn from 8 namespace families (billing, crm, search, docs,
    admin, comms, analytics, infra) using a seeded RNG for reproducibility.

    Args:
        n: Number of items to generate (capped at available pool size).
        seed: Random seed for deterministic output.

    Returns:
        A list of JSON-compatible dicts suitable for :func:`load_catalog_dicts`.
    """
    rng = random.Random(seed)

    # Build the full pool deterministically (sorted by family then by name)
    pool: list[dict[str, Any]] = []
    for family in sorted(_SAMPLE_FAMILIES):
        for suffix, description, tags in _SAMPLE_FAMILIES[family]:
            item_id = f"{family}.{suffix}"
            pool.append(
                {
                    "id": item_id,
                    "kind": "tool",
                    "name": suffix.replace(".", "_"),
                    "description": description,
                    "tags": sorted(tags),
                    "namespace": family,
                    "args_schema": {},
                    "side_effects": rng.random() < 0.3,
                    "cost_hint": round(rng.uniform(0.0, 0.5), 2),
                    "metadata": {},
                }
            )

    # Deterministic shuffle then take first n
    rng.shuffle(pool)
    selected = sorted(pool[:n], key=lambda d: d["id"])
    return selected
