"""Focused tests for the isolated D1 capability-snapshot experiment."""

from __future__ import annotations

import json
from pathlib import Path

from contextweaver.d1 import (
    _validate_snapshot,
    build_snapshot,
    diff_snapshots,
    load_snapshot,
    main,
)


def _write_mcp(
    path: Path,
    *,
    description: str = "Search invoices",
    required: bool = False,
) -> None:
    schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
    }
    if required:
        schema["required"] = ["query"]
    path.write_text(
        json.dumps(
            {
                "_source": "example-server",
                "tools": [
                    {
                        "name": "billing.search_invoices",
                        "description": description,
                        "inputSchema": schema,
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_openapi(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "openapi": "3.1.0",
                "info": {"title": "Example", "version": "1"},
                "paths": {
                    "/invoices": {
                        "get": {
                            "operationId": "listInvoices",
                            "summary": "List invoices",
                            "responses": {"200": {"description": "OK"}},
                        }
                    }
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_snapshot_is_path_independent_and_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "a.json"
    second = tmp_path / "elsewhere.json"
    _write_mcp(first)
    second.write_bytes(first.read_bytes())

    one = build_snapshot(first, "mcp")
    two = build_snapshot(second, "mcp")

    assert one == two
    assert one["capabilities"][0]["id"] == "mcp:billing.search_invoices"
    assert one["capability_digest"].startswith("sha256:")
    assert not _validate_snapshot(one)


def test_mcp_schema_change_is_one_contract_change_not_remove_add(tmp_path: Path) -> None:
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    _write_mcp(before_path, required=False)
    _write_mcp(after_path, required=True)

    report = diff_snapshots(
        build_snapshot(before_path, "mcp"),
        build_snapshot(after_path, "mcp"),
    )

    assert report["added"] == []
    assert report["removed"] == []
    assert report["has_changes"] is True
    assert report["changed"] == [
        {
            "id": "mcp:billing.search_invoices",
            "classification": "contract_changed",
            "risk": "potentially_breaking",
            "paths": ["/args_schema/required", "/normalized_id"],
        }
    ]


def test_description_only_change_is_separated_from_contract_change(tmp_path: Path) -> None:
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    _write_mcp(before_path, description="Search invoices")
    _write_mcp(after_path, description="Search invoices by customer or date")

    report = diff_snapshots(
        build_snapshot(before_path, "mcp"),
        build_snapshot(after_path, "mcp"),
    )

    assert report["changed"] == [
        {
            "id": "mcp:billing.search_invoices",
            "classification": "documentation_only",
            "risk": "none",
            "paths": ["/description"],
        }
    ]


def test_openapi_source_normalizes_without_network(tmp_path: Path) -> None:
    source = tmp_path / "openapi.json"
    _write_openapi(source)

    snapshot = build_snapshot(source, "openapi")

    assert snapshot["source"]["type"] == "openapi"
    assert [item["id"] for item in snapshot["capabilities"]] == ["openapi:listInvoices"]
    assert not _validate_snapshot(snapshot)


def test_verify_rejects_tampered_capability_payload(tmp_path: Path) -> None:
    source = tmp_path / "mcp.json"
    snapshot_path = tmp_path / "snapshot.json"
    _write_mcp(source)
    snapshot = build_snapshot(source, "mcp")
    snapshot["capabilities"][0]["description"] = "tampered"
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    assert "capability_digest does not match capabilities" in _validate_snapshot(snapshot)
    assert main(["verify", str(snapshot_path)]) == 1


def test_diff_check_exit_code_is_opt_in(tmp_path: Path) -> None:
    before_source = tmp_path / "before-source.json"
    after_source = tmp_path / "after-source.json"
    before_snapshot = tmp_path / "before-snapshot.json"
    after_snapshot = tmp_path / "after-snapshot.json"
    _write_mcp(before_source)
    _write_mcp(after_source, description="Changed")
    before_snapshot.write_text(
        json.dumps(build_snapshot(before_source, "mcp")), encoding="utf-8"
    )
    after_snapshot.write_text(
        json.dumps(build_snapshot(after_source, "mcp")), encoding="utf-8"
    )

    assert load_snapshot(before_snapshot)["schema"].endswith("@1")
    assert main(["diff", str(before_snapshot), str(after_snapshot)]) == 0
    assert main(["diff", str(before_snapshot), str(after_snapshot), "--check"]) == 1
