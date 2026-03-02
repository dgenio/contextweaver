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
import random
from pathlib import Path
from typing import Any

from contextweaver.envelope import HydrationResult
from contextweaver.exceptions import CatalogError, ItemNotFoundError
from contextweaver.types import SelectableItem


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
            full item and its schema details.

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


def load_catalog_json(path: str | Path) -> list[SelectableItem]:
    """Load :class:`SelectableItem` objects from a JSON file.

    The file must contain a JSON array of item dicts, each with at least
    ``id``, ``kind``, ``name``, and ``description`` fields.

    Args:
        path: Filesystem path to a JSON file.

    Returns:
        A list of :class:`SelectableItem` objects.

    Raises:
        CatalogError: If the file cannot be read or parsed, or if any item
            dict is missing required fields.
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
    return load_catalog_dicts(data)


def load_catalog_dicts(data: list[dict[str, Any]]) -> list[SelectableItem]:
    """Convert a list of raw dicts into :class:`SelectableItem` objects.

    Each dict must have at least ``id``, ``kind``, ``name``, and
    ``description`` keys.

    Args:
        data: A list of JSON-compatible dicts.

    Returns:
        A list of :class:`SelectableItem` objects.

    Raises:
        CatalogError: If any dict is missing required fields or has invalid
            values.
    """
    required = {"id", "kind", "name", "description"}
    items: list[SelectableItem] = []
    for i, raw in enumerate(data):
        if not isinstance(raw, dict):
            raise CatalogError(f"Item at index {i} is not a dict.")
        missing = required - set(raw)
        if missing:
            raise CatalogError(f"Item at index {i} missing required fields: {sorted(missing)}")
        try:
            items.append(SelectableItem.from_dict(raw))
        except (KeyError, TypeError, ValueError) as exc:
            raise CatalogError(f"Item at index {i} has invalid data: {exc}") from exc
    return items


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
