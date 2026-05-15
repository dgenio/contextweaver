"""Tests for contextweaver.routing.tool_id (gateway_spec.md §1)."""

from __future__ import annotations

import pytest

from contextweaver.exceptions import CatalogError
from contextweaver.routing.tool_id import (
    ToolIdParts,
    canonical_tool_id,
    compute_hash8,
    format_tool_id,
    parse_tool_id,
)

# ---------------------------------------------------------------------------
# format_tool_id — grammar acceptance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "parts,expected",
    [
        (ToolIdParts("github", "create_issue", "1.4.0", None), "github:create_issue@1.4.0"),
        (
            ToolIdParts("slack", "slack_send_message", None, "3a91c7d2"),
            "slack:slack_send_message#3a91c7d2",
        ),
        (ToolIdParts("mcp", "read_file", None, "7b2f0e14"), "mcp:read_file#7b2f0e14"),
        (ToolIdParts("weather", "get", "2024-05", None), "weather:get@2024-05"),
        (ToolIdParts("a", "b", "1", "0123abcd"), "a:b@1#0123abcd"),
    ],
)
def test_format_tool_id_well_formed(parts: ToolIdParts, expected: str) -> None:
    assert format_tool_id(parts) == expected


def test_format_tool_id_requires_hash_when_version_absent() -> None:
    with pytest.raises(CatalogError, match="hash8"):
        format_tool_id(ToolIdParts(namespace="x", name="y", version=None, hash8=None))


def test_format_tool_id_rejects_uppercase_namespace() -> None:
    with pytest.raises(CatalogError, match="namespace"):
        format_tool_id(ToolIdParts(namespace="GitHub", name="x", version="1", hash8=None))


def test_format_tool_id_rejects_invalid_hash() -> None:
    with pytest.raises(CatalogError, match="hash8"):
        format_tool_id(ToolIdParts(namespace="g", name="x", version=None, hash8="GG"))


def test_format_tool_id_rejects_invalid_version() -> None:
    with pytest.raises(CatalogError, match="version"):
        format_tool_id(ToolIdParts(namespace="g", name="x", version="bad/version", hash8=None))


def test_parse_tool_id_length_limit() -> None:
    """parse_tool_id enforces the 240-char total bound on its input."""
    with pytest.raises(CatalogError, match="240"):
        parse_tool_id("a:" + "b" * 300)


# ---------------------------------------------------------------------------
# parse_tool_id — round-trip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "s,namespace,name,version,hash8",
    [
        ("github:create_issue@1.4.0", "github", "create_issue", "1.4.0", None),
        ("slack:slack_send_message#3a91c7d2", "slack", "slack_send_message", None, "3a91c7d2"),
        ("mcp:read_file#7b2f0e14", "mcp", "read_file", None, "7b2f0e14"),
        ("weather:get@2024-05", "weather", "get", "2024-05", None),
        ("a:b@1#0123abcd", "a", "b", "1", "0123abcd"),
    ],
)
def test_parse_tool_id_round_trip(
    s: str, namespace: str, name: str, version: str | None, hash8: str | None
) -> None:
    parts = parse_tool_id(s)
    assert parts.namespace == namespace
    assert parts.name == name
    assert parts.version == version
    assert parts.hash8 == hash8
    assert format_tool_id(parts) == s


def test_parse_tool_id_missing_namespace() -> None:
    with pytest.raises(CatalogError, match="namespace separator"):
        parse_tool_id("nosep")


def test_parse_tool_id_oversized() -> None:
    with pytest.raises(CatalogError, match="240"):
        parse_tool_id("a:" + "b" * 300)


def test_parse_tool_id_not_a_string() -> None:
    with pytest.raises(CatalogError, match="must be a string"):
        parse_tool_id(42)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# compute_hash8 — determinism and §1.3 algorithm
# ---------------------------------------------------------------------------


def test_compute_hash8_deterministic() -> None:
    schema = {"properties": {"name": {"type": "string"}}, "required": ["name"]}
    assert compute_hash8("foo", schema) == compute_hash8("foo", schema)


def test_compute_hash8_independent_of_property_types() -> None:
    """Per §1.3 the canonical shape includes property NAMES only, not types."""
    a = compute_hash8("foo", {"properties": {"x": {"type": "string"}}})
    b = compute_hash8("foo", {"properties": {"x": {"type": "integer"}}})
    assert a == b


def test_compute_hash8_changes_with_property_set() -> None:
    a = compute_hash8("foo", {"properties": {"x": {}}})
    b = compute_hash8("foo", {"properties": {"x": {}, "y": {}}})
    assert a != b


def test_compute_hash8_disambiguates_upstream_names() -> None:
    """Per §1.3: same schema-shape across different upstream names → different hash."""
    shape = {"properties": {"id": {}}, "required": ["id"]}
    assert compute_hash8("github.create_issue", shape) != compute_hash8(
        "gitlab.create_issue", shape
    )


def test_compute_hash8_empty_schema() -> None:
    """Empty / None schema must still produce a valid 8-char hash."""
    h = compute_hash8("read_file", None)
    assert len(h) == 8
    assert all(c in "0123456789abcdef" for c in h)
    assert h == compute_hash8("read_file", {})


# ---------------------------------------------------------------------------
# canonical_tool_id — end-to-end helpers
# ---------------------------------------------------------------------------


def test_canonical_tool_id_with_version_omits_hash() -> None:
    s = canonical_tool_id(
        namespace="github",
        name="create_issue",
        upstream_name="github.create_issue",
        input_schema={"properties": {"title": {}}},
        version="1.4.0",
    )
    parts = parse_tool_id(s)
    assert parts.version == "1.4.0"
    assert parts.hash8 is None


def test_canonical_tool_id_without_version_requires_hash() -> None:
    s = canonical_tool_id(
        namespace="slack",
        name="slack_send_message",
        upstream_name="slack_send_message",
        input_schema={"properties": {"text": {}}},
        version=None,
    )
    parts = parse_tool_id(s)
    assert parts.version is None
    assert parts.hash8 is not None and len(parts.hash8) == 8
