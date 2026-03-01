"""Tests for contextweaver.routing.router."""

from __future__ import annotations

import pytest

from contextweaver.exceptions import RouteError
from contextweaver.routing.catalog import Catalog
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import SelectableItem


def _item(iid: str, name: str, description: str, tags: list[str] | None = None) -> SelectableItem:
    return SelectableItem(id=iid, kind="tool", name=name, description=description, tags=tags or [])


def _catalog() -> Catalog:
    catalog = Catalog()
    catalog.register(_item("db_read", "read_db", "Read from database", tags=["data"]))
    catalog.register(_item("db_write", "write_db", "Write to database", tags=["data"]))
    catalog.register(_item("send_email", "send_email", "Send email notification", tags=["comm"]))
    return catalog


def test_route_returns_paths() -> None:
    catalog = _catalog()
    tree = TreeBuilder().build(catalog)
    router = Router(catalog, beam_width=3)
    paths = router.route("database", tree)
    assert len(paths) >= 1
    assert all(isinstance(p, list) for p in paths)


def test_route_invalid_start() -> None:
    catalog = _catalog()
    tree = TreeBuilder().build(catalog)
    router = Router(catalog)
    with pytest.raises(RouteError):
        router.route("query", tree, start="nonexistent")


def test_route_deterministic() -> None:
    catalog = _catalog()
    tree = TreeBuilder().build(catalog)
    router = Router(catalog, beam_width=3)
    p1 = router.route("read database", tree)
    p2 = router.route("read database", tree)
    assert p1 == p2
