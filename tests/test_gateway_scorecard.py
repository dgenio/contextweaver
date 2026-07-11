"""Tests for contextweaver.adapters.gateway_scorecard."""

from __future__ import annotations

import csv
import io
import json

import pytest

from contextweaver.adapters.gateway_scorecard import (
    Scorecard,
    build_scorecard,
    render_csv,
    render_markdown,
)
from contextweaver.diagnostics import DiagnosticEvent
from contextweaver.exceptions import ConfigError
from contextweaver.routing.catalog_metadata import InventoryMetadata, attach_inventory
from contextweaver.types import SelectableItem


def _item(
    iid: str,
    name: str,
    description: str,
    namespace: str,
    args_schema: dict | None = None,
) -> SelectableItem:
    return SelectableItem(
        id=iid,
        kind="tool",
        name=name,
        description=description,
        namespace=namespace,
        args_schema=args_schema or {},
    )


def _catalog() -> list[SelectableItem]:
    create = attach_inventory(
        _item(
            "github.create_issue",
            "create_issue",
            "Create a new GitHub issue",
            "github",
            args_schema={
                "type": "object",
                "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
            },
        ),
        InventoryMetadata(
            owner_team="platform",
            business_domain="dev-tools",
            risk_level="medium",
            lifecycle="active",
        ),
    )
    delete = attach_inventory(
        _item("jira.delete_issue", "delete_issue", "Delete a Jira ticket permanently", "jira"),
        InventoryMetadata(
            owner_team="workflow",
            business_domain="workflow",
            risk_level="high",
            lifecycle="deprecated",
        ),
    )
    listing = _item(
        "github.list_issues", "list_issues", "List open issues in a repository", "github"
    )
    docs = _item(
        "search.docs", "search_documentation", "Search product documentation pages", "search"
    )
    return [create, listing, delete, docs]


def _event(
    name: str,
    tool_id: str | None = None,
    *,
    success: bool = True,
    duration_ms: float | None = None,
    attributes: dict | None = None,
) -> DiagnosticEvent:
    return DiagnosticEvent(
        event=name,
        success=success,
        duration_ms=duration_ms,
        session_id="s1",
        tool_id=tool_id,
        attributes=attributes or {},
    )


def _events() -> list[DiagnosticEvent]:
    return [
        _event("catalog.loaded", attributes={"tool_count": 4}),
        _event(
            "browse.completed",
            duration_ms=4.0,
            attributes={"tool_ids": ["github.create_issue", "github.list_issues"]},
        ),
        _event(
            "browse.completed",
            duration_ms=3.0,
            attributes={"tool_ids": ["github.create_issue", "ghost.tool"]},
        ),
        _event("browse.failed", success=False, attributes={"error_code": "PATH_NOT_FOUND"}),
        _event("execute.completed", "github.create_issue", duration_ms=10.0),
        _event("execute.completed", "github.create_issue", duration_ms=20.0),
        _event("execute.completed", "github.create_issue", duration_ms=100.0),
        _event("execute.failed", "github.create_issue", success=False, duration_ms=5.0),
        _event("execute.failed", "jira.delete_issue", success=False, duration_ms=1.0),
        _event("execute.failed", "jira.delete_issue", success=False, duration_ms=2.0),
        _event("execute.failed", "jira.delete_issue", success=False, duration_ms=3.0),
        _event("hydrate.completed", "github.create_issue", duration_ms=1.0),
        _event("view.completed", duration_ms=1.0, attributes={"artifact_ref": "text:1"}),
    ]


def test_totals_and_inventory_counts() -> None:
    card = build_scorecard(_catalog(), _events())
    assert card.total_tools == 4
    assert card.total_namespaces == 3
    assert card.by_owner == {"platform": 1, "unknown": 2, "workflow": 1}
    assert card.by_domain == {"dev-tools": 1, "unknown": 2, "workflow": 1}
    assert card.by_risk == {"high": 1, "medium": 1, "unknown": 2}
    assert card.by_lifecycle == {"active": 1, "deprecated": 1, "unknown": 2}


def test_most_executed_and_most_routed_orderings() -> None:
    card = build_scorecard(_catalog(), _events())
    assert card.most_executed == [
        {"tool_id": "github.create_issue", "count": 4},
        {"tool_id": "jira.delete_issue", "count": 3},
    ]
    # Ties (count 1) break alphabetically by tool_id.
    assert card.most_routed == [
        {"tool_id": "github.create_issue", "count": 2},
        {"tool_id": "ghost.tool", "count": 1},
        {"tool_id": "github.list_issues", "count": 1},
    ]


def test_unused_deprecated_and_selected_not_executed() -> None:
    card = build_scorecard(_catalog(), _events())
    assert card.unused_tools == ["github.list_issues", "search.docs"]
    assert card.deprecated_in_use == ["jira.delete_issue"]
    assert card.selected_not_executed == ["ghost.tool", "github.list_issues"]


def test_p95_latency_known_values() -> None:
    items = [_item("ns.tool", "tool", "A tool under test", "ns")]
    events = [
        _event("execute.completed", "ns.tool", duration_ms=float(ms))
        for ms in range(10, 210, 10)  # 10, 20, ..., 200 (20 samples)
    ]
    card = build_scorecard(items, events)
    assert card.highest_latency == [{"tool_id": "ns.tool", "p95_ms": 190.0, "calls": 20}]


def test_highest_latency_ordering() -> None:
    card = build_scorecard(_catalog(), _events())
    # create_issue: p95 of [5, 10, 20, 100] -> 100.0; delete_issue: p95 of [1, 2, 3] -> 3.0
    assert card.highest_latency == [
        {"tool_id": "github.create_issue", "p95_ms": 100.0, "calls": 4},
        {"tool_id": "jira.delete_issue", "p95_ms": 3.0, "calls": 3},
    ]


def test_failure_rate_requires_three_calls_and_sorts_desc() -> None:
    card = build_scorecard(_catalog(), _events())
    assert card.highest_failure_rate == [
        {"tool_id": "jira.delete_issue", "failure_rate": 1.0, "calls": 3},
        {"tool_id": "github.create_issue", "failure_rate": 0.25, "calls": 4},
    ]
    # A tool with only two calls never appears, even if both failed.
    two_calls = [
        _event("execute.failed", "github.list_issues", success=False),
        _event("execute.failed", "github.list_issues", success=False),
    ]
    card = build_scorecard(_catalog(), two_calls)
    assert card.highest_failure_rate == []


def test_largest_schema_ordering_and_top_n_cap() -> None:
    card = build_scorecard(_catalog(), _events(), top_n=2)
    assert [row["tool_id"] for row in card.largest_schema] == [
        "github.create_issue",
        "github.list_issues",  # empty-schema tie broken by id
    ]
    assert len(card.most_executed) <= 2
    assert len(card.highest_latency) <= 2


def test_collision_counts_present_and_zero_filled() -> None:
    card = build_scorecard(_catalog(), _events())
    assert card.collision_counts == {
        "exact_name": 0,
        "near_name": 0,
        "similar_description": 0,
        "similar_schema": 0,
    }


def test_deterministic_double_run_and_input_order_invariance() -> None:
    first = build_scorecard(_catalog(), _events())
    second = build_scorecard(list(reversed(_catalog())), _events())
    assert first.to_dict() == second.to_dict()


def test_to_dict_json_round_trips() -> None:
    card = build_scorecard(_catalog(), _events())
    payload = json.loads(json.dumps(card.to_dict()))
    assert payload["total_tools"] == 4
    assert payload["deprecated_in_use"] == ["jira.delete_issue"]
    assert payload["version"] == 1
    assert isinstance(Scorecard(**{k: v for k, v in payload.items()}), Scorecard)


def test_render_csv_parses_with_csv_module() -> None:
    text = render_csv(build_scorecard(_catalog(), _events()))
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == ["section", "key", "value"]
    assert all(len(row) == 3 for row in rows)
    assert ["totals", "tools", "4"] in rows
    assert ["deprecated_in_use", "jira.delete_issue", "true"] in rows
    assert ["highest_failure_rate", "jira.delete_issue", "1.0"] in rows


def test_render_markdown_leads_with_findings() -> None:
    text = render_markdown(build_scorecard(_catalog(), _events()))
    assert text.startswith("# Tool-Surface Health Scorecard")
    findings = text.index("## Findings")
    assert findings < text.index("### Deprecated tools in use") < text.index("## Totals")
    assert "`jira.delete_issue`" in text
    assert "### Unused tools (zero executions)" in text
    assert "### Highest failure rate" in text


def test_empty_events_marks_everything_unused() -> None:
    card = build_scorecard(_catalog(), [])
    assert card.unused_tools == sorted(item.id for item in _catalog())
    assert card.most_executed == []
    assert card.deprecated_in_use == []
    assert card.highest_latency == []


def test_top_n_must_be_positive() -> None:
    with pytest.raises(ConfigError):
        build_scorecard(_catalog(), _events(), top_n=0)
