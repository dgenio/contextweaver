"""Tests for contextweaver.context.firewall."""

from __future__ import annotations

from contextweaver.context.firewall import apply_firewall, apply_firewall_to_batch
from contextweaver.store.artifacts import InMemoryArtifactStore
from contextweaver.types import ContextItem, ItemKind


def test_non_tool_result_passthrough() -> None:
    item = ContextItem(id="u1", kind=ItemKind.user_turn, text="hello")
    store = InMemoryArtifactStore()
    processed, env = apply_firewall(item, store)
    assert processed is item
    assert env is None
    assert len(store.list_refs()) == 0


def test_tool_result_intercepted() -> None:
    item = ContextItem(
        id="r1", kind=ItemKind.tool_result, text="status: ok\nresult: 42 rows\n- row1\n- row2"
    )
    store = InMemoryArtifactStore()
    processed, env = apply_firewall(item, store)
    assert env is not None
    assert env.status == "ok"
    # Raw content stored in artifact store
    assert store.get(f"artifact:{item.id}") is not None
    # Processed item has shorter text (summary)
    assert len(processed.text) <= len(item.text)
    assert processed.artifact_ref is not None


def test_firewall_extracts_facts() -> None:
    item = ContextItem(
        id="r2", kind=ItemKind.tool_result, text="status: ok\ncount: 5\n1. first\n2. second"
    )
    store = InMemoryArtifactStore()
    _, env = apply_firewall(item, store)
    assert env is not None
    assert len(env.facts) >= 1


def test_apply_firewall_to_batch() -> None:
    items = [
        ContextItem(id="u1", kind=ItemKind.user_turn, text="hello"),
        ContextItem(id="r1", kind=ItemKind.tool_result, text="raw output here"),
        ContextItem(id="a1", kind=ItemKind.agent_msg, text="agent response"),
    ]
    store = InMemoryArtifactStore()
    processed, envelopes = apply_firewall_to_batch(items, store)
    assert len(processed) == 3
    assert len(envelopes) == 1
    assert envelopes[0].provenance["source_item_id"] == "r1"
