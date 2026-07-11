"""Tests for contextweaver.routing.collision (issue #381).

Covers the four finding kinds, false-positive suppression (exact pairs not
re-reported as near-name; tiny schemas skipped), the recommendation heuristic
(incl. the deprecated-vs-active case via the issue #377 inventory seam),
deterministic ordering, serde, and the Markdown rendering.
"""

from __future__ import annotations

import pytest

from contextweaver.exceptions import ConfigError
from contextweaver.routing.collision import (
    FINDING_KINDS,
    CollisionFinding,
    CollisionReport,
    analyze_collisions,
)
from contextweaver.types import SelectableItem


def _item(
    item_id: str,
    name: str,
    namespace: str = "ns",
    description: str = "",
    properties: list[str] | None = None,
    lifecycle: str | None = None,
    domain: str | None = None,
) -> SelectableItem:
    metadata: dict[str, object] = {}
    inventory: dict[str, object] = {}
    if lifecycle is not None:
        inventory["lifecycle"] = lifecycle
    if domain is not None:
        inventory["business_domain"] = domain
    if inventory:
        metadata = {"_contextweaver": {"inventory": inventory}}
    args_schema: dict[str, object] = {}
    if properties is not None:
        args_schema = {"type": "object", "properties": {prop: {} for prop in properties}}
    return SelectableItem(
        id=item_id,
        kind="tool",
        name=name,
        description=description,
        namespace=namespace,
        args_schema=dict(args_schema),
        metadata=dict(metadata),
    )


def _findings(report: CollisionReport, kind: str) -> list[CollisionFinding]:
    return [finding for finding in report.findings if finding.kind == kind]


# ---------------------------------------------------------------------------
# exact_name
# ---------------------------------------------------------------------------


def test_exact_name_across_namespaces() -> None:
    report = analyze_collisions(
        [
            _item("github:search", "search", namespace="github"),
            _item("jira:search", "search", namespace="jira"),
            _item("github:create_issue", "create_issue", namespace="github"),
        ]
    )
    exact = _findings(report, "exact_name")
    assert len(exact) == 1
    assert exact[0].item_ids == ["github:search", "jira:search"]
    assert exact[0].score == 1.0
    assert exact[0].recommendation == "namespace"
    assert "search" in exact[0].evidence
    assert report.counts["exact_name"] == 1


def test_same_name_same_namespace_is_not_exact_collision() -> None:
    report = analyze_collisions(
        [_item("ns:a", "search", namespace="ns"), _item("ns:b", "search", namespace="ns")]
    )
    assert _findings(report, "exact_name") == []


# ---------------------------------------------------------------------------
# near_name
# ---------------------------------------------------------------------------


def test_near_name_camel_vs_snake() -> None:
    report = analyze_collisions(
        [
            _item("a:get_user", "get_user", namespace="a"),
            _item("b:getUser", "getUser", namespace="b"),
        ]
    )
    near = _findings(report, "near_name")
    assert len(near) == 1
    assert near[0].item_ids == ["a:get_user", "b:getUser"]
    assert near[0].score == 1.0
    assert near[0].recommendation == "rename"


def test_near_name_respects_threshold() -> None:
    items = [
        _item("a:alpha_one", "alpha_one", namespace="a"),
        _item("b:zebra_xyz", "zebra_xyz", namespace="b"),
    ]
    assert _findings(analyze_collisions(items), "near_name") == []


def test_exact_pairs_not_rereported_as_near_name() -> None:
    report = analyze_collisions(
        [
            _item("github:search", "search", namespace="github"),
            _item("jira:search", "search", namespace="jira"),
        ]
    )
    assert len(_findings(report, "exact_name")) == 1
    assert _findings(report, "near_name") == []


# ---------------------------------------------------------------------------
# similar_description
# ---------------------------------------------------------------------------


def test_similar_description_same_domain_recommends_consolidate() -> None:
    desc = "Search customer invoices by date range."
    report = analyze_collisions(
        [
            _item("a:invoice_search", "invoice_search", description=desc, domain="billing"),
            _item("b:billing_lookup", "billing_lookup", description=desc, domain="billing"),
        ]
    )
    similar = _findings(report, "similar_description")
    assert len(similar) == 1
    assert similar[0].score == 1.0
    assert similar[0].recommendation == "consolidate"


def test_similar_description_without_shared_domain_recommends_review() -> None:
    desc = "Search customer invoices by date range."
    report = analyze_collisions(
        [
            _item("a:invoice_search", "invoice_search", description=desc, domain="billing"),
            _item("b:billing_lookup", "billing_lookup", description=desc),
        ]
    )
    similar = _findings(report, "similar_description")
    assert len(similar) == 1
    assert similar[0].recommendation == "review"


def test_empty_descriptions_are_skipped() -> None:
    report = analyze_collisions(
        [_item("a:one", "one", description=""), _item("b:two", "two", description="   ")]
    )
    assert _findings(report, "similar_description") == []


# ---------------------------------------------------------------------------
# similar_schema
# ---------------------------------------------------------------------------


def test_similar_schema_over_property_names() -> None:
    props = ["query", "limit", "offset"]
    report = analyze_collisions(
        [
            _item("a:fetch_alpha", "fetch_alpha", description="Alpha metrics", properties=props),
            _item("b:zzz_report", "zzz_report", description="Zeta summaries", properties=props),
        ]
    )
    schema = _findings(report, "similar_schema")
    assert len(schema) == 1
    assert schema[0].score == 1.0
    assert schema[0].recommendation == "review"
    assert "limit" in schema[0].evidence


def test_small_schemas_are_skipped() -> None:
    # False-positive suppression: single-property schemas overlap by chance.
    report = analyze_collisions(
        [
            _item("a:fetch_alpha", "fetch_alpha", properties=["query"]),
            _item("b:zzz_report", "zzz_report", properties=["query"]),
        ]
    )
    assert _findings(report, "similar_schema") == []


# ---------------------------------------------------------------------------
# Recommendation heuristic: deprecated vs active
# ---------------------------------------------------------------------------


def test_deprecated_vs_active_pair_recommends_deprecate() -> None:
    props = ["query", "limit"]
    report = analyze_collisions(
        [
            _item("a:fetch_alpha", "fetch_alpha", properties=props, lifecycle="deprecated"),
            _item("b:zzz_report", "zzz_report", properties=props, lifecycle="active"),
        ]
    )
    schema = _findings(report, "similar_schema")
    assert len(schema) == 1
    assert schema[0].recommendation == "deprecate"


# ---------------------------------------------------------------------------
# Determinism and report shape
# ---------------------------------------------------------------------------


def test_output_is_deterministic_and_input_order_independent() -> None:
    items = [
        _item("github:search", "search", namespace="github"),
        _item("jira:search", "search", namespace="jira"),
        _item("a:get_user", "get_user", namespace="a"),
        _item("b:getUser", "getUser", namespace="b"),
        _item("x:alpha", "alpha", description="Compute alpha statistics daily"),
        _item("y:beta", "beta", description="Compute alpha statistics daily"),
    ]
    first = analyze_collisions(items)
    second = analyze_collisions(list(reversed(items)))
    assert first.to_dict() == second.to_dict()
    keys = [(finding.kind, finding.item_ids[0]) for finding in first.findings]
    assert keys == sorted(keys)


def test_empty_catalog_yields_empty_report() -> None:
    report = analyze_collisions([])
    assert report.findings == []
    assert report.counts == dict.fromkeys(FINDING_KINDS, 0)


def test_report_roundtrip() -> None:
    report = analyze_collisions(
        [
            _item("github:search", "search", namespace="github"),
            _item("jira:search", "search", namespace="jira"),
        ]
    )
    restored = CollisionReport.from_dict(report.to_dict())
    assert restored.to_dict() == report.to_dict()


def test_finding_from_dict_rejects_bad_enums() -> None:
    good = analyze_collisions(
        [
            _item("github:search", "search", namespace="github"),
            _item("jira:search", "search", namespace="jira"),
        ]
    ).findings[0]
    with pytest.raises(ConfigError):
        CollisionFinding.from_dict({**good.to_dict(), "kind": "sorta_similar"})
    with pytest.raises(ConfigError):
        CollisionFinding.from_dict({**good.to_dict(), "recommendation": "shrug"})


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def test_render_markdown_contains_counts_and_findings() -> None:
    report = analyze_collisions(
        [
            _item("gh|search", "search", namespace="github"),
            _item("jira:search", "search", namespace="jira"),
        ]
    )
    assert report.findings, "expected an exact_name finding to render"
    rendered = report.render_markdown()
    assert "# Catalog Collision Report" in rendered
    for kind in FINDING_KINDS:
        assert f"`{kind}`: {report.counts[kind]}" in rendered
    assert f"Total findings: {len(report.findings)}" in rendered
    assert "jira:search" in rendered
    # Pipe characters in catalog ids are escaped so the table stays valid.
    assert "gh\\|search" in rendered
