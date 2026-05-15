"""Tests for contextweaver.routing.path (gateway_spec.md §3)."""

from __future__ import annotations

import pytest

from contextweaver.exceptions import PathInvalidError, PathNotFoundError
from contextweaver.routing.graph import ChoiceGraph
from contextweaver.routing.path import parse_path, resolve_path

# ---------------------------------------------------------------------------
# parse_path — grammar acceptance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/", []),
        ("/github", ["github"]),
        ("/github/issues", ["github", "issues"]),
        ("/github/issues/create_issue", ["github", "issues", "create_issue"]),
        ("/github/issues/*", ["github", "issues", "*"]),
        ("/a/b-c/0_d", ["a", "b-c", "0_d"]),
    ],
)
def test_parse_path_accepts_valid(path: str, expected: list[str]) -> None:
    assert parse_path(path) == expected


# ---------------------------------------------------------------------------
# parse_path — grammar rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,match",
    [
        ("", "empty"),
        ("github", "start with '/'"),
        ("/github/", "end with"),
        ("//foo", "empty segment"),
        ("/GitHub", "must start with"),  # uppercase root segment
        ("/0digit", "root segment.*must start with"),
        ("/a/*/b", "final segment"),
        ("/a/b!c", "grammar"),
    ],
)
def test_parse_path_rejects_invalid(path: str, match: str) -> None:
    with pytest.raises(PathInvalidError, match=match):
        parse_path(path)


def test_parse_path_non_string() -> None:
    with pytest.raises(PathInvalidError, match="must be a string"):
        parse_path(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# resolve_path — traversal
# ---------------------------------------------------------------------------


def _three_namespace_graph() -> ChoiceGraph:
    """Build a small ChoiceGraph with three namespace clusters."""
    graph = ChoiceGraph(max_children=10)
    graph.add_node("root", label="root")
    graph.add_node("root/github", label="GitHub")
    graph.add_node("root/slack", label="Slack")
    graph.add_node("root/weather", label="Weather")
    graph.add_edge("root", "root/github")
    graph.add_edge("root", "root/slack")
    graph.add_edge("root", "root/weather")
    # GitHub leaf items
    graph.add_item("github:create_issue@1.0#abcdef01")
    graph.add_item("github:close_issue@1.0#abcdef02")
    graph.add_edge("root/github", "github:create_issue@1.0#abcdef01")
    graph.add_edge("root/github", "github:close_issue@1.0#abcdef02")
    return graph


def test_resolve_path_root_returns_namespace_children() -> None:
    graph = _three_namespace_graph()
    ids = resolve_path(graph, [])
    # graph.successors returns sorted node IDs.
    assert ids == ["root/github", "root/slack", "root/weather"]


def test_resolve_path_walks_into_namespace() -> None:
    graph = _three_namespace_graph()
    ids = resolve_path(graph, ["github"])
    assert ids == [
        "github:close_issue@1.0#abcdef02",
        "github:create_issue@1.0#abcdef01",
    ]


def test_resolve_path_lands_on_leaf() -> None:
    graph = _three_namespace_graph()
    ids = resolve_path(graph, ["github", "create_issue"])
    # No children → single leaf returned.
    assert ids == ["github:create_issue@1.0#abcdef01"]


def test_resolve_path_wildcard_equivalent_to_omitting() -> None:
    graph = _three_namespace_graph()
    a = resolve_path(graph, ["github"])
    b = resolve_path(graph, ["github", "*"])
    assert a == b


def test_resolve_path_missing_segment_raises() -> None:
    graph = _three_namespace_graph()
    with pytest.raises(PathNotFoundError, match="missing"):
        resolve_path(graph, ["missing"])


def test_resolve_path_missing_leaf_raises() -> None:
    graph = _three_namespace_graph()
    with pytest.raises(PathNotFoundError):
        resolve_path(graph, ["github", "no_such_tool"])
