"""Tool catalog management for the contextweaver Routing Engine.

Functions for loading, generating, and validating catalogs of SelectableItems.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from contextweaver.exceptions import CatalogError
from contextweaver.types import SelectableItem


def load_catalog_json(path: str | Path) -> list[SelectableItem]:
    """Load from JSON. Raises CatalogError on invalid data."""
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError(f"Failed to load catalog: {exc}") from exc
    if not isinstance(data, list):
        raise CatalogError("Catalog JSON must be a list of items")
    return load_catalog_dicts(data)


def load_catalog_dicts(data: list[dict[str, Any]]) -> list[SelectableItem]:
    """Load from dicts. Raises CatalogError on invalid/missing fields."""
    items: list[SelectableItem] = []
    seen_ids: set[str] = set()
    for i, raw in enumerate(data):
        for field in ("id", "kind", "name", "description"):
            if field not in raw:
                raise CatalogError(f"Item {i} missing required field {field!r}")
        if raw["id"] in seen_ids:
            raise CatalogError(f"Duplicate item id: {raw['id']!r}")
        seen_ids.add(raw["id"])
        items.append(SelectableItem.from_dict(raw))
    return items


def generate_sample_catalog(n: int = 80, seed: int = 42) -> list[dict[str, Any]]:
    """Deterministic sample catalog across 6+ namespace families."""
    rng = random.Random(seed)

    families: dict[str, list[tuple[str, str, str]]] = {
        "billing": [
            ("invoices.search", "Search invoices by date range or status", "search,billing"),
            ("invoices.create", "Create a new invoice for a customer", "create,billing"),
            ("invoices.get", "Get invoice details by ID", "read,billing"),
            ("payments.process", "Process a payment against an invoice", "payment,billing"),
            ("payments.refund", "Refund a processed payment", "payment,billing,refund"),
            ("payments.list", "List recent payments", "list,billing,payment"),
            ("reports.revenue", "Generate revenue report for a period", "report,billing"),
            ("reports.aging", "Generate accounts receivable aging report", "report,billing"),
            ("reports.forecast", "Forecast future revenue", "report,billing,forecast"),
        ],
        "crm": [
            ("contacts.find", "Find contacts by name or email", "search,crm"),
            ("contacts.create", "Create a new contact record", "create,crm"),
            ("contacts.update", "Update contact information", "update,crm"),
            ("deals.list", "List active deals in pipeline", "list,crm,deals"),
            ("deals.create", "Create a new deal", "create,crm,deals"),
            ("deals.close", "Close a deal as won or lost", "update,crm,deals"),
            ("activities.log", "Log a customer activity", "create,crm,activity"),
            ("activities.recent", "Get recent activities for a contact", "read,crm,activity"),
        ],
        "search": [
            ("documents.search", "Full-text search across documents", "search,documents"),
            ("documents.index", "Index a new document for search", "create,documents"),
            ("web.search", "Search the web for information", "search,web"),
            ("web.scrape", "Extract content from a web page", "read,web"),
            ("internal.search", "Search internal knowledge base", "search,internal"),
            ("internal.suggest", "Suggest related documents", "search,internal,suggest"),
        ],
        "docs": [
            ("pages.create", "Create a new documentation page", "create,docs"),
            ("pages.update", "Update an existing page", "update,docs"),
            ("pages.get", "Retrieve a page by ID", "read,docs"),
            ("pages.list", "List pages in a space", "list,docs"),
            ("templates.list", "List available page templates", "list,docs,templates"),
            ("templates.apply", "Apply a template to create a page", "create,docs,templates"),
        ],
        "admin": [
            ("users.list", "List all users in the organization", "list,admin,users"),
            ("users.create", "Create a new user account", "create,admin,users"),
            ("users.disable", "Disable a user account", "update,admin,users"),
            ("roles.list", "List available roles", "list,admin,roles"),
            ("roles.assign", "Assign a role to a user", "update,admin,roles"),
            ("audit.log", "Query the audit log", "read,admin,audit"),
            ("audit.export", "Export audit log as CSV", "read,admin,audit,export"),
        ],
        "comms": [
            ("email.send", "Send an email message", "send,comms,email"),
            ("email.draft", "Create an email draft", "create,comms,email"),
            ("slack.post", "Post a message to a Slack channel", "send,comms,slack"),
            ("slack.thread", "Reply to a Slack thread", "send,comms,slack"),
            ("notifications.send", "Send a push notification", "send,comms,notifications"),
            ("notifications.schedule", "Schedule a notification", "create,comms,notifications"),
        ],
    }

    cost_hints = ["free", "low", "medium", "high"]
    kinds = ["tool", "agent", "skill", "internal"]

    items: list[dict[str, Any]] = []
    all_entries: list[tuple[str, str, str, str]] = []

    for ns, entries in families.items():
        for name_suffix, desc, tags in entries:
            all_entries.append((ns, name_suffix, desc, tags))

    rng.shuffle(all_entries)

    for i, (ns, name_suffix, desc, tags) in enumerate(all_entries[:n]):
        full_name = f"{ns}.{name_suffix}"
        kind = "tool" if rng.random() < 0.7 else rng.choice(kinds)
        items.append(
            {
                "id": full_name,
                "kind": kind,
                "name": full_name,
                "description": desc,
                "tags": tags.split(","),
                "namespace": ns,
                "args_schema": None,
                "side_effects": rng.random() < 0.3,
                "cost_hint": rng.choice(cost_hints),
                "metadata": {"generated": True, "index": i},
            }
        )

    # Ensure deterministic order
    items.sort(key=lambda x: x["id"])
    return items
