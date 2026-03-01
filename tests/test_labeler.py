"""Tests for contextweaver.routing.labeler -- KeywordLabeler with various item groups."""

from __future__ import annotations

from contextweaver.routing.labeler import KeywordLabeler
from contextweaver.types import SelectableItem


def _item(
    name: str, description: str, tags: list[str] | None = None, namespace: str = ""
) -> SelectableItem:
    return SelectableItem(
        id=name,
        kind="tool",
        name=name,
        description=description,
        tags=tags or [],
        namespace=namespace,
    )


class TestKeywordLabeler:
    """Tests for the KeywordLabeler class."""

    def test_empty_items(self) -> None:
        labeler = KeywordLabeler()
        label, hint = labeler.label([])
        assert label == "empty"
        assert "No tools" in hint

    def test_single_namespace_dominant(self) -> None:
        labeler = KeywordLabeler(namespace_threshold=0.6)
        items = [
            _item("billing.search", "Search invoices", ["billing"], "billing"),
            _item("billing.create", "Create invoice", ["billing"], "billing"),
            _item("billing.get", "Get invoice", ["billing"], "billing"),
        ]
        label, hint = labeler.label(items)
        assert "billing" in label
        assert "Tools related to" in hint

    def test_mixed_namespaces_no_dominant(self) -> None:
        labeler = KeywordLabeler(namespace_threshold=0.6)
        items = [
            _item("billing.search", "Search invoices", ["billing"], "billing"),
            _item("crm.find", "Find contacts", ["crm"], "crm"),
            _item("admin.list", "List users", ["admin"], "admin"),
        ]
        label, hint = labeler.label(items)
        # No dominant namespace, should use keyword-based labeling
        assert "billing" not in label or "crm" not in label

    def test_label_uses_top_k_tokens(self) -> None:
        labeler = KeywordLabeler(top_k=2)
        items = [
            _item("search_tool", "Search database records", ["search"]),
            _item("find_tool", "Search and find data in database", ["search"]),
        ]
        label, hint = labeler.label(items)
        # "search" and "database" should be common tokens
        assert isinstance(label, str)
        assert len(label) > 0

    def test_routing_hint_format(self) -> None:
        labeler = KeywordLabeler()
        items = [_item("t1", "Some tool description", ["tag1"])]
        _, hint = labeler.label(items)
        assert hint.startswith("Tools related to")
