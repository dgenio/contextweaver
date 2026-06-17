"""Tests for contextweaver.routing.primitive_id (gateway_spec.md §9, #671)."""

from __future__ import annotations

import pytest

from contextweaver.exceptions import CatalogError
from contextweaver.routing.primitive_id import (
    PRIMITIVE_KINDS,
    PrimitiveIdParts,
    canonical_prompt_id,
    canonical_resource_id,
    compute_prompt_hash8,
    compute_resource_hash8,
    format_primitive_id,
    parse_primitive_id,
    resolve_collisions,
)
from contextweaver.routing.tool_id import ToolIdParts, format_tool_id, parse_tool_id

# ---------------------------------------------------------------------------
# format / parse round-trip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "parts,expected",
    [
        (
            PrimitiveIdParts("tool", "github", "create_issue", "1.4.0", None),
            "github:create_issue@1.4.0",
        ),
        (
            PrimitiveIdParts("resource", "fs", "readme", None, "ab12cd34"),
            "resource::fs:readme#ab12cd34",
        ),
        (
            PrimitiveIdParts("prompt", "gh", "summarize", None, "deadbeef"),
            "prompt::gh:summarize#deadbeef",
        ),
        (
            PrimitiveIdParts("resource", "db", "users", "2024-05", None),
            "resource::db:users@2024-05",
        ),
    ],
)
def test_format_primitive_id_well_formed(parts: PrimitiveIdParts, expected: str) -> None:
    assert format_primitive_id(parts) == expected


@pytest.mark.parametrize(
    "text",
    ["github:create_issue@1.4.0", "resource::fs:readme#ab12cd34", "prompt::gh:summarize#deadbeef"],
)
def test_primitive_id_round_trips(text: str) -> None:
    assert format_primitive_id(parse_primitive_id(text)) == text


def test_tool_kind_renders_bare_and_matches_tool_grammar() -> None:
    """A ``tool`` primitive id is byte-identical to the bare §1 tool_id."""
    parts = PrimitiveIdParts("tool", "slack", "send", None, "3a91c7d2")
    rendered = format_primitive_id(parts)
    assert rendered == "slack:send#3a91c7d2"
    # And it parses cleanly through the existing tool_id grammar.
    assert parse_tool_id(rendered).namespace == "slack"


def test_resource_and_prompt_ids_are_disjoint_from_tool_ids() -> None:
    """The ``::`` separator keeps non-tool ids out of the §1 tool space."""
    resource = canonical_resource_id(namespace="fs", name="readme", uri="file:///readme.md")
    prompt = canonical_prompt_id(namespace="gh", name="summarize", argument_names=["repo"])
    # A tool id can never contain ``::`` (single-colon grammar), so parsing the
    # resource id as a bare tool id would mis-split — confirm the prefix is present.
    assert resource.startswith("resource::")
    assert prompt.startswith("prompt::")
    assert parse_primitive_id(resource).kind == "resource"
    assert parse_primitive_id(prompt).kind == "prompt"


# ---------------------------------------------------------------------------
# grammar rejection
# ---------------------------------------------------------------------------


def test_format_rejects_unknown_kind() -> None:
    with pytest.raises(CatalogError, match="primitive kind"):
        format_primitive_id(PrimitiveIdParts("widget", "ns", "x", None, "0123abcd"))  # type: ignore[arg-type]


def test_format_rejects_invalid_namespace() -> None:
    with pytest.raises(CatalogError, match="namespace"):
        format_primitive_id(PrimitiveIdParts("resource", "Bad", "x", None, "0123abcd"))


def test_format_requires_hash_when_version_absent() -> None:
    with pytest.raises(CatalogError, match="hash8"):
        format_primitive_id(PrimitiveIdParts("prompt", "ns", "x", None, None))


def test_parse_rejects_unknown_kind_prefix() -> None:
    with pytest.raises(CatalogError, match="unknown kind prefix"):
        parse_primitive_id("widget::ns:x#0123abcd")


def test_parse_rejects_explicit_tool_prefix() -> None:
    """``tool::…`` is not canonical — tools render bare, so it is rejected."""
    with pytest.raises(CatalogError, match="unknown kind prefix"):
        parse_primitive_id("tool::ns:x#0123abcd")


def test_parse_rejects_non_string() -> None:
    with pytest.raises(CatalogError, match="must be a string"):
        parse_primitive_id(123)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# shape hashes
# ---------------------------------------------------------------------------


def test_resource_hash_is_stable_and_uri_sensitive() -> None:
    h1 = compute_resource_hash8("file:///a.md")
    assert h1 == compute_resource_hash8("file:///a.md")
    assert h1 != compute_resource_hash8("file:///b.md")
    assert len(h1) == 8 and all(c in "0123456789abcdef" for c in h1)


def test_prompt_hash_ignores_argument_order_but_not_set() -> None:
    same = compute_prompt_hash8("p", ["b", "a"]) == compute_prompt_hash8("p", ["a", "b"])
    assert same
    assert compute_prompt_hash8("p", ["a"]) != compute_prompt_hash8("p", ["a", "b"])
    assert compute_prompt_hash8("p", None) == compute_prompt_hash8("p", [])


def test_resource_and_prompt_hashes_are_domain_separated() -> None:
    """Resource and prompt hashing use distinct domain prefixes (no clash)."""
    assert compute_resource_hash8("x") != compute_prompt_hash8("x", None)


# ---------------------------------------------------------------------------
# serialisation
# ---------------------------------------------------------------------------


def test_parts_round_trip_through_dict() -> None:
    parts = PrimitiveIdParts("resource", "fs", "readme", None, "ab12cd34")
    assert PrimitiveIdParts.from_dict(parts.to_dict()) == parts


def test_from_dict_rejects_unknown_kind() -> None:
    with pytest.raises(CatalogError, match="primitive kind"):
        PrimitiveIdParts.from_dict({"kind": "widget", "namespace": "n", "name": "x"})


# ---------------------------------------------------------------------------
# collision policy
# ---------------------------------------------------------------------------


def test_resolve_collisions_disambiguates_deterministically() -> None:
    # Use canonical ids (8-hex-char `hash8` per §1.1) so the test exercises the
    # same id shape `resolve_collisions` sees in production.
    assignment = resolve_collisions(
        ["fs:readme#ab12cd34", "fs:readme#ab12cd34", "gh:notes#0123abcd"]
    )
    assert assignment == {
        "0": "fs:readme#ab12cd34",
        "1": "fs:readme#ab12cd34~2",
        "2": "gh:notes#0123abcd",
    }


def test_resolve_collisions_is_order_independent_for_the_winner() -> None:
    """The lowest input index always keeps the bare id, regardless of grouping."""
    assignment = resolve_collisions(
        ["gh:notes#0123abcd", "fs:readme#ab12cd34", "fs:readme#ab12cd34"]
    )
    assert assignment["1"] == "fs:readme#ab12cd34"  # first occurrence
    assert assignment["2"] == "fs:readme#ab12cd34~2"  # second occurrence
    assert assignment["0"] == "gh:notes#0123abcd"


def test_resolve_collisions_handles_triple() -> None:
    assignment = resolve_collisions(["svc:tool#deadbeef", "svc:tool#deadbeef", "svc:tool#deadbeef"])
    assert assignment == {
        "0": "svc:tool#deadbeef",
        "1": "svc:tool#deadbeef~2",
        "2": "svc:tool#deadbeef~3",
    }


def test_primitive_kinds_constant() -> None:
    assert PRIMITIVE_KINDS == ("tool", "resource", "prompt")


def test_unused_tool_id_helpers_still_importable() -> None:
    """Sanity: the shared §1 helpers this module builds on remain importable."""
    assert format_tool_id(ToolIdParts("ns", "x", None, "0123abcd")) == "ns:x#0123abcd"
