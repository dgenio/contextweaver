"""Tests for contextweaver.routing.manifest (issue #48)."""

from __future__ import annotations

import time

from contextweaver.routing.manifest import (
    MANIFEST_VERSION,
    GraphManifest,
    compute_catalog_hash,
)
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import SelectableItem


def _item(
    iid: str,
    *,
    name: str = "",
    description: str = "desc",
    namespace: str = "",
    tags: list[str] | None = None,
) -> SelectableItem:
    return SelectableItem(
        id=iid,
        kind="tool",
        name=name or iid,
        description=description,
        namespace=namespace,
        tags=tags or [],
    )


# ------------------------------------------------------------------
# compute_catalog_hash
# ------------------------------------------------------------------


def test_catalog_hash_invariant_under_reordering() -> None:
    items_a = [_item("a"), _item("b"), _item("c")]
    items_b = [_item("c"), _item("a"), _item("b")]
    assert compute_catalog_hash(items_a) == compute_catalog_hash(items_b)


def test_catalog_hash_differs_when_id_changes() -> None:
    h1 = compute_catalog_hash([_item("a")])
    h2 = compute_catalog_hash([_item("b")])
    assert h1 != h2


def test_catalog_hash_differs_when_description_changes() -> None:
    h1 = compute_catalog_hash([_item("a", description="foo")])
    h2 = compute_catalog_hash([_item("a", description="bar")])
    assert h1 != h2


def test_catalog_hash_differs_when_tags_change() -> None:
    h1 = compute_catalog_hash([_item("a", tags=["x"])])
    h2 = compute_catalog_hash([_item("a", tags=["y"])])
    assert h1 != h2


def test_catalog_hash_invariant_under_tag_reordering() -> None:
    h1 = compute_catalog_hash([_item("a", tags=["x", "y"])])
    h2 = compute_catalog_hash([_item("a", tags=["y", "x"])])
    assert h1 == h2


def test_catalog_hash_ignores_metadata() -> None:
    """Metadata edits do not invalidate the routing graph."""
    a = _item("a")
    b = _item("a")
    b.metadata = {"unrelated": "value"}
    assert compute_catalog_hash([a]) == compute_catalog_hash([b])


# ------------------------------------------------------------------
# GraphManifest
# ------------------------------------------------------------------


def test_for_build_populates_fields() -> None:
    items = [_item("a"), _item("b")]
    m = GraphManifest.for_build(
        items,
        strategy="auto",
        max_depth=3,
        seed=42,
        engine_versions={"retriever": "tfidf-1"},
    )
    assert m.manifest_version == MANIFEST_VERSION
    assert m.build_hash == compute_catalog_hash(items)
    assert m.seed == 42
    assert m.engine_versions == {"retriever": "tfidf-1"}
    assert m.item_count == 2
    assert m.strategy == "auto"
    assert m.max_depth == 3
    # Default timestamp is wall-clock.
    assert m.timestamp > 0.0


def test_for_build_explicit_timestamp() -> None:
    m = GraphManifest.for_build([_item("a")], timestamp=0.0)
    assert m.timestamp == 0.0


def test_round_trip() -> None:
    items = [_item("a", tags=["x"])]
    m = GraphManifest.for_build(items, strategy="namespace", max_depth=5, seed=7)
    restored = GraphManifest.from_dict(m.to_dict())
    assert restored == m


def test_matches_catalog_true_when_unchanged() -> None:
    items = [_item("a"), _item("b")]
    m = GraphManifest.for_build(items)
    assert m.matches_catalog(items)


def test_matches_catalog_false_when_changed() -> None:
    items = [_item("a")]
    m = GraphManifest.for_build(items)
    assert not m.matches_catalog([_item("b")])


def test_matches_catalog_false_when_hash_empty() -> None:
    """A manifest without a build_hash never matches."""
    m = GraphManifest()
    assert not m.matches_catalog([_item("a")])


# ------------------------------------------------------------------
# Integration with TreeBuilder
# ------------------------------------------------------------------


def test_tree_builder_attaches_manifest() -> None:
    items = [_item(f"i{i}", namespace="ns") for i in range(5)]
    graph = TreeBuilder().build(items)
    manifest = graph.manifest
    assert manifest is not None
    assert manifest.item_count == 5
    assert manifest.build_hash == compute_catalog_hash(items)


def test_tree_builder_uses_deterministic_timestamp() -> None:
    """build() must produce identical manifests for identical inputs."""
    items = [_item(f"i{i}", namespace="ns") for i in range(5)]
    g1 = TreeBuilder().build(items)
    g2 = TreeBuilder().build(items)
    assert g1.manifest is not None
    assert g2.manifest is not None
    assert g1.manifest == g2.manifest


def test_manifest_survives_round_trip() -> None:
    from contextweaver.routing.graph import ChoiceGraph

    items = [_item(f"i{i}", namespace="ns") for i in range(3)]
    graph = TreeBuilder().build(items)
    restored = ChoiceGraph.from_dict(graph.to_dict())
    assert restored.manifest is not None
    assert restored.manifest.build_hash == graph.manifest.build_hash  # type: ignore[union-attr]


def test_manifest_attribute_setter() -> None:
    """Callers can replace the manifest with a wall-clock variant."""
    items = [_item("a")]
    graph = TreeBuilder().build(items)
    real = GraphManifest.for_build(items, timestamp=time.time())
    graph.manifest = real
    assert graph.manifest is not None
    assert graph.manifest.timestamp > 0.0


def test_manifest_records_max_children_in_extra() -> None:
    """The effective ``max_children`` is persisted under ``manifest.extra``.

    The :class:`TreeBuilder` docstring promises that the
    ``max_children`` setting (whether default, explicit, or sourced
    from a :class:`RoutingConfig`) is recorded on the manifest.  This
    test pins that contract for both the default constructor and the
    explicit value.
    """
    items = [_item(f"i{i}", namespace="ns") for i in range(3)]

    default_graph = TreeBuilder().build(items)
    assert default_graph.manifest is not None
    assert default_graph.manifest.extra.get("max_children") == 20

    custom_graph = TreeBuilder(max_children=4).build(items)
    assert custom_graph.manifest is not None
    assert custom_graph.manifest.extra.get("max_children") == 4
