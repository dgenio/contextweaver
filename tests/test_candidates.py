"""Tests for contextweaver.context.candidates -- generate_candidates filtering, redaction, policy."""

from __future__ import annotations

from contextweaver.config import ContextPolicy
from contextweaver.context.candidates import generate_candidates
from contextweaver.types import ContextItem, ItemKind, Phase


def _item(
    iid: str,
    kind: ItemKind = ItemKind.USER_TURN,
    text: str = "text",
    sensitivity: str = "public",
    tags: list[str] | None = None,
    ttl_seconds: float | None = None,
    timestamp: float = 0.0,
) -> ContextItem:
    meta: dict = {"timestamp": timestamp, "sensitivity": sensitivity}
    if tags:
        meta["tags"] = tags
    if ttl_seconds is not None:
        meta["ttl_seconds"] = ttl_seconds
    return ContextItem(id=iid, kind=kind, text=text, token_estimate=len(text) // 4, metadata=meta)


class TestGenerateCandidates:
    """Tests for generate_candidates."""

    def test_filters_by_phase_route(self) -> None:
        items = [
            _item("u1", ItemKind.USER_TURN),
            _item("r1", ItemKind.TOOL_RESULT),
            _item("p1", ItemKind.POLICY),
            _item("d1", ItemKind.DOC_SNIPPET),
        ]
        policy = ContextPolicy()
        candidates = generate_candidates(items, Phase.ROUTE, policy)
        kinds = {c.kind for c in candidates}
        assert ItemKind.USER_TURN in kinds
        assert ItemKind.POLICY in kinds
        assert ItemKind.TOOL_RESULT not in kinds
        assert ItemKind.DOC_SNIPPET not in kinds

    def test_answer_phase_allows_all_kinds(self) -> None:
        items = [_item(f"item_{kind.value}", kind) for kind in ItemKind]
        policy = ContextPolicy()
        candidates = generate_candidates(items, Phase.ANSWER, policy)
        assert len(candidates) == len(ItemKind)

    def test_sensitivity_filtering(self) -> None:
        items = [
            _item("pub", sensitivity="public"),
            _item("int", sensitivity="internal"),
            _item("conf", sensitivity="confidential"),
            _item("rest", sensitivity="restricted"),
        ]
        policy = ContextPolicy()
        # Default floor is CONFIDENTIAL, so RESTRICTED should be dropped
        candidates = generate_candidates(items, Phase.ANSWER, policy)
        ids = {c.id for c in candidates}
        assert "pub" in ids
        assert "int" in ids
        assert "conf" in ids
        assert "rest" not in ids

    def test_redaction_hook(self) -> None:
        class DropBillingHook:
            def redact(self, item: ContextItem) -> ContextItem | None:
                if "billing" in item.metadata.get("tags", []):
                    return None
                return item

        items = [
            _item("u1", tags=["billing"]),
            _item("u2", tags=["crm"]),
        ]
        policy = ContextPolicy(redaction_hooks=[DropBillingHook()])
        candidates = generate_candidates(items, Phase.ANSWER, policy)
        ids = {c.id for c in candidates}
        assert "u1" not in ids
        assert "u2" in ids

    def test_preserves_order(self) -> None:
        items = [_item(f"item_{i}", ItemKind.USER_TURN) for i in range(5)]
        policy = ContextPolicy()
        candidates = generate_candidates(items, Phase.ANSWER, policy)
        assert [c.id for c in candidates] == [f"item_{i}" for i in range(5)]
