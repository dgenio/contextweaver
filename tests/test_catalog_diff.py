"""Tests for contextweaver.routing.catalog_diff."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from contextweaver.exceptions import ConfigError
from contextweaver.routing.catalog_diff import (
    CatalogDiff,
    RoutingImpact,
    diff_catalogs,
    routing_impact,
    suggest_probes,
)
from contextweaver.types import SelectableItem


def _item(iid: str, name: str, description: str, tags: list[str] | None = None) -> SelectableItem:
    return SelectableItem(id=iid, kind="tool", name=name, description=description, tags=tags or [])


def _base_items() -> list[SelectableItem]:
    return [
        _item("db_read", "read_db", "Read rows from the analytics database"),
        _item("db_write", "write_db", "Write rows to the analytics database"),
        _item("search_docs", "search_docs", "Search documentation pages for a phrase"),
        _item("send_email", "send_email", "Send an email notification to a user"),
    ]


# ------------------------------------------------------------------
# diff_catalogs
# ------------------------------------------------------------------


def test_diff_detects_added_removed_and_changed() -> None:
    before = _base_items()
    after = [item for item in before if item.id != "send_email"]
    after[1] = replace(after[1], description="Write and upsert rows in the database")
    after.append(_item("create_user", "create_user", "Create a new user account"))
    diff = diff_catalogs(before, after)
    assert diff.added == ["create_user"]
    assert diff.removed == ["send_email"]
    assert diff.changed == [{"id": "db_write", "fields": ["description"]}]
    assert diff.hash_before != diff.hash_after


def test_diff_reports_multiple_changed_fields_sorted() -> None:
    before = _base_items()
    after = list(before)
    after[0] = replace(after[0], description="Changed", tags=["new"], namespace="analytics")
    diff = diff_catalogs(before, after)
    assert diff.changed == [{"id": "db_read", "fields": ["description", "namespace", "tags"]}]


def test_diff_identical_catalogs_is_empty_with_equal_hashes() -> None:
    diff = diff_catalogs(_base_items(), _base_items())
    assert diff.added == [] and diff.removed == [] and diff.changed == []
    assert diff.hash_before == diff.hash_after


def test_diff_rejects_duplicate_ids() -> None:
    duplicated = _base_items() + [_item("db_read", "read_db_2", "Duplicate id")]
    with pytest.raises(ConfigError):
        diff_catalogs(duplicated, _base_items())
    with pytest.raises(ConfigError):
        diff_catalogs(_base_items(), duplicated)


def test_diff_serde_round_trip_and_markdown() -> None:
    before = _base_items()
    diff = diff_catalogs(before, before[:-1])
    assert CatalogDiff.from_dict(json.loads(json.dumps(diff.to_dict()))) == diff
    text = diff.render_markdown()
    assert text.startswith("# Catalog Diff")
    assert "`send_email`" in text
    assert diff.hash_before in text


# ------------------------------------------------------------------
# routing_impact
# ------------------------------------------------------------------


def test_rename_flips_top1_for_probe() -> None:
    before = _base_items()
    after = [
        replace(item, id="notify_user") if item.id == "send_email" else item for item in before
    ]
    impact = routing_impact(before, after, [("send an email notification", "send_email")], top_k=5)
    assert impact.probes_total == 1
    assert impact.top1_changed == 1
    assert impact.examples == [
        {
            "query": "send an email notification",
            "before_top1": "send_email",
            "after_top1": "notify_user",
        }
    ]
    assert impact.recall_before == 1.0
    assert impact.recall_after == 0.0


def test_recall_drops_when_expected_tool_removed() -> None:
    before = _base_items()
    after = [item for item in before if item.id != "send_email"]
    probes: list[tuple[str, str | None]] = [
        ("send an email notification", "send_email"),
        ("read rows from the analytics database", "db_read"),
    ]
    impact = routing_impact(before, after, probes)
    assert impact.recall_before == 1.0
    assert impact.recall_after == 0.5


def test_recall_is_none_without_expected_ids() -> None:
    impact = routing_impact(_base_items(), _base_items(), [("read the database", None)])
    assert impact.recall_before is None
    assert impact.recall_after is None
    assert impact.top1_changed == 0
    assert impact.examples == []


def test_examples_capped_at_ten() -> None:
    before = _base_items()
    after = [
        _item(f"other_{i}", f"other_{i}", f"An entirely different capability number {i}")
        for i in range(4)
    ]
    probes: list[tuple[str, str | None]] = [
        (f"database email docs probe {i}", None) for i in range(12)
    ]
    impact = routing_impact(before, after, probes)
    assert impact.probes_total == 12
    assert impact.top1_changed == 12
    assert len(impact.examples) == 10


def test_routing_impact_is_deterministic() -> None:
    before = _base_items()
    after = before[:-1]
    probes = suggest_probes(before)
    first = routing_impact(before, after, list(probes))
    second = routing_impact(before, after, list(probes))
    assert first.to_dict() == second.to_dict()


def test_routing_impact_validation() -> None:
    probes: list[tuple[str, str | None]] = [("q", None)]
    with pytest.raises(ConfigError):
        routing_impact([], _base_items(), probes)
    with pytest.raises(ConfigError):
        routing_impact(_base_items(), [], probes)
    with pytest.raises(ConfigError):
        routing_impact(_base_items(), _base_items(), [])
    with pytest.raises(ConfigError):
        routing_impact(_base_items(), _base_items(), probes, top_k=0)


def test_impact_serde_round_trip_and_markdown() -> None:
    impact = routing_impact(
        _base_items(), _base_items()[:-1], [("send an email notification", "send_email")]
    )
    assert RoutingImpact.from_dict(json.loads(json.dumps(impact.to_dict()))) == impact
    text = impact.render_markdown()
    assert text.startswith("# Routing Impact")
    assert "Recall@5: 1.0000 -> 0.0000" in text
    assert "## Top-1 flips" in text


# ------------------------------------------------------------------
# suggest_probes
# ------------------------------------------------------------------


def test_suggest_probes_deterministic_and_ordered_by_id() -> None:
    items = _base_items()
    assert suggest_probes(items) == suggest_probes(list(reversed(items)))
    expected_ids = [probe[1] for probe in suggest_probes(items)]
    assert expected_ids == sorted(item.id for item in items)


def test_suggest_probes_builds_query_from_name_and_description() -> None:
    probes = suggest_probes(_base_items(), n=1)
    assert probes == [("read db read rows from the analytics database", "db_read")]


def test_suggest_probes_caps_at_n_and_validates() -> None:
    assert len(suggest_probes(_base_items(), n=2)) == 2
    assert len(suggest_probes(_base_items(), n=100)) == 4
    with pytest.raises(ConfigError):
        suggest_probes(_base_items(), n=0)
