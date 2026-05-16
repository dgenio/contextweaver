"""Internal one-shot script that produces the expanded routing_gold.json.

Run once to regenerate ``benchmarks/routing_gold.json`` with 200 entries
(≥20/namespace) and the ``namespace`` top-level field on every entry
(issue #209). The script is checked in for reproducibility but is not
part of the normal build flow — it does not run in ``make ci`` and is
not called from any workflow.

Usage::

    python scripts/_gold_expansion.py
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLD_PATH = REPO_ROOT / "benchmarks" / "routing_gold.json"


_NEW: list[tuple[str, list[str], list[str]]] = [
    # admin (15 new)
    ("download audit trail to CSV", ["admin.audit.export"], ["admin", "audit"]),
    ("show me yesterday's admin actions", ["admin.audit.query"], ["admin", "audit"]),
    ("set audit log expiration to 90 days", ["admin.audit.retention"], ["admin", "audit"]),
    ("view existing roles", ["admin.roles.list"], ["admin", "roles"]),
    ("show all available permission roles", ["admin.roles.list"], ["admin", "roles"]),
    ("what roles can I create", ["admin.roles.list"], ["admin", "roles"]),
    ("reset a user's password", ["admin.users.reset_password"], ["admin", "users"]),
    ("send password reset email", ["admin.users.reset_password"], ["admin", "users"]),
    ("disable user access", ["admin.users.deactivate"], ["admin", "users"]),
    ("find a user by email", ["admin.users.list"], ["admin", "users"]),
    ("onboard a new team member", ["admin.users.create"], ["admin", "users"]),
    ("show the active users", ["admin.users.list"], ["admin", "users"]),
    ("give admin permissions to someone", ["admin.roles.assign"], ["admin", "roles"]),
    ("set who can edit billing", ["admin.roles.assign"], ["admin", "roles"]),
    ("invite a new collaborator", ["admin.users.create"], ["admin", "users"]),
    ("change a user's role", ["admin.roles.assign"], ["admin", "roles"]),
    ("see who has admin access", ["admin.users.list"], ["admin", "users"]),
    # analytics (17 new)
    ("export event data to file", ["analytics.events.export"], ["analytics", "events"]),
    ("download event log", ["analytics.events.export"], ["analytics", "events"]),
    ("send events to BigQuery export", ["analytics.events.export"], ["analytics", "events"]),
    ("register a custom metric", ["analytics.metrics.define"], ["analytics", "metrics"]),
    ("define a new business metric", ["analytics.metrics.define"], ["analytics", "metrics"]),
    ("create a custom KPI", ["analytics.metrics.define"], ["analytics", "metrics"]),
    ("notify me on revenue drop", ["analytics.metrics.alert"], ["analytics", "metrics"]),
    ("set up paging on error rate", ["analytics.metrics.alert"], ["analytics", "metrics"]),
    (
        "find the sales overview dashboard",
        ["analytics.dashboards.list"],
        ["analytics", "dashboards"],
    ),
    ("duplicate a dashboard", ["analytics.dashboards.create"], ["analytics", "dashboards"]),
    ("log a user signup event", ["analytics.events.track"], ["analytics", "events"]),
    ("count signups this week", ["analytics.events.query"], ["analytics", "events"]),
    ("what's the active user count today", ["analytics.metrics.query"], ["analytics", "metrics"]),
    ("how many conversions yesterday", ["analytics.events.query"], ["analytics", "events"]),
    (
        "give dashboard access to a colleague",
        ["analytics.dashboards.share"],
        ["analytics", "dashboards"],
    ),
    (
        "publish dashboard to executive team",
        ["analytics.dashboards.share"],
        ["analytics", "dashboards"],
    ),
    ("plot the weekly active users", ["analytics.metrics.query"], ["analytics", "metrics"]),
    ("export analytics events as CSV", ["analytics.events.export"], ["analytics", "events"]),
    # billing (17 new)
    ("look up invoice INV-1234", ["billing.invoices.get"], ["billing", "invoices"]),
    ("show invoice details", ["billing.invoices.get"], ["billing", "invoices"]),
    ("get payment history for a customer", ["billing.payments.list"], ["billing", "payments"]),
    ("list all transactions this month", ["billing.payments.list"], ["billing", "payments"]),
    ("refund a payment", ["billing.payments.refund"], ["billing", "payments"]),
    ("process refund for order", ["billing.payments.refund"], ["billing", "payments"]),
    ("show overdue invoices", ["billing.reports.aging"], ["billing", "reports"]),
    ("accounts receivable aging report", ["billing.reports.aging"], ["billing", "reports"]),
    ("cancel a subscription", ["billing.subscriptions.cancel"], ["billing", "subscriptions"]),
    ("downgrade a customer's plan", ["billing.subscriptions.update"], ["billing", "subscriptions"]),
    ("upgrade subscription tier", ["billing.subscriptions.update"], ["billing", "subscriptions"]),
    (
        "change billing frequency to annual",
        ["billing.subscriptions.update"],
        ["billing", "subscriptions"],
    ),
    ("issue a refund to customer", ["billing.payments.refund"], ["billing", "payments"]),
    ("monthly revenue summary", ["billing.reports.revenue"], ["billing", "reports"]),
    ("predict next quarter revenue", ["billing.reports.forecast"], ["billing", "reports"]),
    ("void an invoice that was sent in error", ["billing.invoices.void"], ["billing", "invoices"]),
    ("find unpaid invoices from acme corp", ["billing.invoices.search"], ["billing", "invoices"]),
    # comms (17 new)
    ("show recent emails in my inbox", ["comms.email.list"], ["comms", "email"]),
    ("list outgoing emails today", ["comms.email.list"], ["comms", "email"]),
    (
        "subscribe to deployment notifications",
        ["comms.notifications.subscribe"],
        ["comms", "notifications"],
    ),
    ("watch a channel for alerts", ["comms.notifications.subscribe"], ["comms", "notifications"]),
    ("find Slack thread about the outage", ["comms.slack.search"], ["comms", "slack"]),
    ("thumbs up the deployment message", ["comms.slack.react"], ["comms", "slack"]),
    ("compose new email", ["comms.email.draft"], ["comms", "email"]),
    ("schedule an email to be sent later", ["comms.email.draft"], ["comms", "email"]),
    ("use the welcome email template", ["comms.email.template"], ["comms", "email"]),
    ("send mass email to all customers", ["comms.email.send"], ["comms", "email"]),
    ("remind user about pending invoice via email", ["comms.email.send"], ["comms", "email"]),
    ("send Slack message to a channel", ["comms.slack.post"], ["comms", "slack"]),
    ("post incident update in #ops", ["comms.slack.post"], ["comms", "slack"]),
    ("find Slack messages mentioning a customer", ["comms.slack.search"], ["comms", "slack"]),
    ("DM a teammate", ["comms.slack.post"], ["comms", "slack"]),
    ("acknowledge a Slack notification", ["comms.slack.react"], ["comms", "slack"]),
    (
        "get notified when a build finishes",
        ["comms.notifications.subscribe"],
        ["comms", "notifications"],
    ),
    # crm (20 new)
    ("show activity log for a contact", ["crm.activities.list"], ["crm", "activities"]),
    ("list all customer interactions", ["crm.activities.list"], ["crm", "activities"]),
    ("log a sales call", ["crm.activities.log"], ["crm", "activities"]),
    ("record a meeting note", ["crm.activities.log"], ["crm", "activities"]),
    ("track an email interaction", ["crm.activities.log"], ["crm", "activities"]),
    ("how many meetings this week", ["crm.activities.stats"], ["crm", "activities"]),
    ("weekly sales activity stats", ["crm.activities.stats"], ["crm", "activities"]),
    ("create a new CRM contact", ["crm.contacts.create"], ["crm", "contacts"]),
    ("add a customer to the CRM", ["crm.contacts.create"], ["crm", "contacts"]),
    ("merge two contact records", ["crm.contacts.merge"], ["crm", "contacts"]),
    ("consolidate duplicate contacts", ["crm.contacts.merge"], ["crm", "contacts"]),
    ("update contact phone number", ["crm.contacts.update"], ["crm", "contacts"]),
    ("edit a customer's company name", ["crm.contacts.update"], ["crm", "contacts"]),
    ("mark deal as won", ["crm.deals.close"], ["crm", "deals"]),
    ("what deals are in negotiation", ["crm.deals.pipeline"], ["crm", "deals"]),
    ("show deals worth more than $100k", ["crm.deals.search"], ["crm", "deals"]),
    ("find deals closing this month", ["crm.deals.search"], ["crm", "deals"]),
    ("show pipeline by stage", ["crm.deals.pipeline"], ["crm", "deals"]),
    ("lookup contact by phone number", ["crm.contacts.find"], ["crm", "contacts"]),
    ("remove an outdated activity entry", ["crm.activities.delete"], ["crm", "activities"]),
    # docs (21 new)
    ("archive an old documentation page", ["docs.pages.archive"], ["docs", "pages"]),
    ("remove outdated docs", ["docs.pages.archive"], ["docs", "pages"]),
    ("deprecate a help article", ["docs.pages.archive"], ["docs", "pages"]),
    ("create a new help article", ["docs.pages.create"], ["docs", "pages"]),
    ("write a new doc page", ["docs.pages.create"], ["docs", "pages"]),
    ("start a new knowledge base article", ["docs.pages.create"], ["docs", "pages"]),
    ("make a help page live", ["docs.pages.publish"], ["docs", "pages"]),
    ("release a docs draft to production", ["docs.pages.publish"], ["docs", "pages"]),
    ("search the documentation", ["docs.pages.search"], ["docs", "pages"]),
    ("find docs about API authentication", ["docs.pages.search"], ["docs", "pages"]),
    ("look up documentation for the SDK", ["docs.pages.search"], ["docs", "pages"]),
    ("edit an existing help article", ["docs.pages.update"], ["docs", "pages"]),
    ("update the doc with new screenshots", ["docs.pages.update"], ["docs", "pages"]),
    ("modify a published doc", ["docs.pages.update"], ["docs", "pages"]),
    ("use a template for new docs", ["docs.templates.apply"], ["docs", "templates"]),
    ("apply the RFC template to a new page", ["docs.templates.apply"], ["docs", "templates"]),
    ("create a documentation template", ["docs.templates.create"], ["docs", "templates"]),
    ("make a new doc template", ["docs.templates.create"], ["docs", "templates"]),
    ("list available document templates", ["docs.templates.list"], ["docs", "templates"]),
    ("show me all the doc templates", ["docs.templates.list"], ["docs", "templates"]),
    ("browse the documentation library", ["docs.pages.search"], ["docs", "pages"]),
    # infra (20 new)
    ("list all deployments", ["infra.deployments.list"], ["infra", "deployments"]),
    ("show production deploys this week", ["infra.deployments.list"], ["infra", "deployments"]),
    ("what deployed today", ["infra.deployments.list"], ["infra", "deployments"]),
    ("did the deploy succeed", ["infra.deployments.status"], ["infra", "deployments"]),
    ("is the api service healthy", ["infra.deployments.status"], ["infra", "deployments"]),
    ("ship code to staging", ["infra.deployments.create"], ["infra", "deployments"]),
    ("promote a build to production", ["infra.deployments.create"], ["infra", "deployments"]),
    ("revert the last deploy", ["infra.deployments.rollback"], ["infra", "deployments"]),
    (
        "undo a deployment from 30 minutes ago",
        ["infra.deployments.rollback"],
        ["infra", "deployments"],
    ),
    ("search application logs for errors", ["infra.logs.query"], ["infra", "logs"]),
    ("grep logs for a traceback", ["infra.logs.query"], ["infra", "logs"]),
    ("find log entries mentioning a user", ["infra.logs.query"], ["infra", "logs"]),
    ("tail the production server log", ["infra.logs.tail"], ["infra", "logs"]),
    ("stream live logs", ["infra.logs.tail"], ["infra", "logs"]),
    ("watch the deployment log in real time", ["infra.logs.tail"], ["infra", "logs"]),
    ("fetch the API key value", ["infra.secrets.get"], ["infra", "secrets"]),
    ("retrieve a secret from the vault", ["infra.secrets.get"], ["infra", "secrets"]),
    ("rotate the database password", ["infra.secrets.rotate"], ["infra", "secrets"]),
    ("change the API key", ["infra.secrets.rotate"], ["infra", "secrets"]),
    ("update service credentials", ["infra.secrets.rotate"], ["infra", "secrets"]),
    # search (20 new)
    (
        "remove a document from the search index",
        ["search.documents.delete"],
        ["search", "documents"],
    ),
    ("unindex an old document", ["search.documents.delete"], ["search", "documents"]),
    ("delete a stale search entry", ["search.documents.delete"], ["search", "documents"]),
    ("add a document to the index", ["search.documents.index"], ["search", "documents"]),
    ("submit a document for indexing", ["search.documents.index"], ["search", "documents"]),
    ("register new content for search", ["search.documents.index"], ["search", "documents"]),
    ("look up a doc by content", ["search.documents.query"], ["search", "documents"]),
    ("scrape a web page for content", ["search.web.scrape"], ["search", "web"]),
    ("extract text from a URL", ["search.web.scrape"], ["search", "web"]),
    ("fetch the contents of a website", ["search.web.scrape"], ["search", "web"]),
    ("look up a company online", ["search.web.search"], ["search", "web"]),
    ("find press releases about a competitor", ["search.web.search"], ["search", "web"]),
    ("give me a TL;DR of this article", ["search.web.summarize"], ["search", "web"]),
    ("summarize this blog post", ["search.web.summarize"], ["search", "web"]),
    ("search inside the company wiki", ["search.internal.search"], ["search", "internal"]),
    ("find pages mentioning a project", ["search.internal.search"], ["search", "internal"]),
    ("as I type, show search suggestions", ["search.internal.suggest"], ["search", "internal"]),
    ("autocomplete a query", ["search.internal.suggest"], ["search", "internal"]),
    ("rebuild the search index", ["search.internal.reindex"], ["search", "internal"]),
    ("refresh the full-text index", ["search.internal.reindex"], ["search", "internal"]),
]


def _to_entry(query: str, expected: Sequence[str], tags: Sequence[str]) -> dict[str, object]:
    """Return a sorted-keys gold entry with the namespace field populated."""
    namespace = expected[0].split(".")[0]
    return {
        "expected": list(expected),
        "namespace": namespace,
        "query": query,
        "tags": list(tags),
    }


def _add_namespace_field(entry: dict[str, object]) -> dict[str, object]:
    """Augment an existing entry with the namespace field, preserving order."""
    namespace = str(entry["expected"][0]).split(".")[0]  # type: ignore[index]
    return {
        "expected": entry["expected"],
        "namespace": namespace,
        "query": entry["query"],
        "tags": entry["tags"],
    }


def main() -> None:
    """Regenerate the gold file in place. Existing 50 entries are preserved verbatim."""
    existing = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    augmented = [_add_namespace_field(e) for e in existing]
    new = [_to_entry(q, exp, tags) for q, exp, tags in _NEW]
    out = augmented + new
    GOLD_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(out)} entries ({len(augmented)} preserved + {len(new)} new) to {GOLD_PATH}")


if __name__ == "__main__":
    main()
