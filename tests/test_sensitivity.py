"""Tests for contextweaver.context.sensitivity."""

from __future__ import annotations

import pytest

from contextweaver.config import ContextPolicy
from contextweaver.context.manager import ContextManager
from contextweaver.context.sensitivity import (
    _SENSITIVITY_ORDER,
    MaskRedactionHook,
    apply_sensitivity_filter,
)
from contextweaver.store.event_log import InMemoryEventLog
from contextweaver.types import ContextItem, ItemKind, Phase, Sensitivity


def _item(
    iid: str,
    sensitivity: Sensitivity = Sensitivity.public,
    text: str = "hello",
    kind: ItemKind = ItemKind.user_turn,
) -> ContextItem:
    return ContextItem(id=iid, kind=kind, text=text, sensitivity=sensitivity)


# ------------------------------------------------------------------
# Sensitivity ordering
# ------------------------------------------------------------------


def test_sensitivity_order_monotonic() -> None:
    levels = [
        Sensitivity.public,
        Sensitivity.internal,
        Sensitivity.confidential,
        Sensitivity.restricted,
    ]
    for i in range(len(levels) - 1):
        assert _SENSITIVITY_ORDER[levels[i]] < _SENSITIVITY_ORDER[levels[i + 1]]


# ------------------------------------------------------------------
# Drop mode (default)
# ------------------------------------------------------------------


def test_drop_restricted_when_floor_confidential() -> None:
    policy = ContextPolicy(sensitivity_floor=Sensitivity.confidential)
    items = [
        _item("pub", Sensitivity.public, "public data"),
        _item("int", Sensitivity.internal, "internal data"),
        _item("conf", Sensitivity.confidential, "confidential data"),
        _item("restr", Sensitivity.restricted, "SSN: 123-45-6789"),
    ]
    filtered, dropped = apply_sensitivity_filter(items, policy)
    assert dropped == 2
    kept_ids = {i.id for i in filtered}
    assert "pub" in kept_ids
    assert "int" in kept_ids
    assert "conf" not in kept_ids
    assert "restr" not in kept_ids


def test_public_internal_pass_through_unmodified() -> None:
    policy = ContextPolicy(sensitivity_floor=Sensitivity.confidential)
    pub = _item("pub", Sensitivity.public, "public text")
    intern = _item("int", Sensitivity.internal, "internal text")
    filtered, dropped = apply_sensitivity_filter([pub, intern], policy)
    assert dropped == 0
    assert len(filtered) == 2
    assert filtered[0].text == "public text"
    assert filtered[1].text == "internal text"


def test_floor_restricted_drops_only_restricted() -> None:
    policy = ContextPolicy(sensitivity_floor=Sensitivity.restricted)
    items = [
        _item("pub", Sensitivity.public),
        _item("int", Sensitivity.internal),
        _item("conf", Sensitivity.confidential),
        _item("restr", Sensitivity.restricted),
    ]
    filtered, dropped = apply_sensitivity_filter(items, policy)
    assert dropped == 1
    kept_ids = {i.id for i in filtered}
    assert "pub" in kept_ids
    assert "int" in kept_ids
    assert "conf" in kept_ids
    assert "restr" not in kept_ids


def test_drop_mode_records_in_build_stats() -> None:
    log = InMemoryEventLog()
    log.append(_item("pub", Sensitivity.public, "safe"))
    log.append(_item("secret", Sensitivity.restricted, "SSN: 123-45-6789"))
    policy = ContextPolicy(sensitivity_floor=Sensitivity.confidential)
    mgr = ContextManager(event_log=log, policy=policy)
    pack = mgr.build_sync(phase=Phase.answer, query="hello")
    assert "SSN" not in pack.prompt
    assert pack.stats.dropped_reasons.get("sensitivity", 0) >= 1


# ------------------------------------------------------------------
# Redact mode
# ------------------------------------------------------------------


def test_redact_mode_replaces_text() -> None:
    policy = ContextPolicy(
        sensitivity_floor=Sensitivity.confidential,
        sensitivity_action="redact",
    )
    items = [
        _item("pub", Sensitivity.public, "safe text"),
        _item("conf", Sensitivity.confidential, "secret text"),
    ]
    filtered, dropped = apply_sensitivity_filter(items, policy)
    assert dropped == 0
    assert len(filtered) == 2
    assert filtered[0].text == "safe text"
    assert filtered[1].text == "[REDACTED: confidential]"


def test_redact_mode_preserves_metadata() -> None:
    policy = ContextPolicy(
        sensitivity_floor=Sensitivity.confidential,
        sensitivity_action="redact",
    )
    original = ContextItem(
        id="s1",
        kind=ItemKind.doc_snippet,
        text="secret doc content",
        sensitivity=Sensitivity.restricted,
        metadata={"source": "vault"},
        parent_id="parent1",
    )
    filtered, _ = apply_sensitivity_filter([original], policy)
    assert len(filtered) == 1
    redacted = filtered[0]
    assert redacted.id == "s1"
    assert redacted.kind == ItemKind.doc_snippet
    assert redacted.text == "[REDACTED: restricted]"
    assert redacted.metadata == {"source": "vault"}
    assert redacted.parent_id == "parent1"
    assert redacted.sensitivity == Sensitivity.restricted


def test_redact_mode_with_explicit_mask_hook() -> None:
    policy = ContextPolicy(
        sensitivity_floor=Sensitivity.restricted,
        sensitivity_action="redact",
        redaction_hooks=["mask"],
    )
    item = _item("r1", Sensitivity.restricted, "top secret")
    filtered, dropped = apply_sensitivity_filter([item], policy)
    assert dropped == 0
    assert filtered[0].text == "[REDACTED: restricted]"


# ------------------------------------------------------------------
# MaskRedactionHook directly
# ------------------------------------------------------------------


def test_mask_hook_replaces_text() -> None:
    hook = MaskRedactionHook()
    item = _item("x", Sensitivity.confidential, "original text")
    redacted = hook.redact(item)
    assert redacted.text == "[REDACTED: confidential]"
    assert redacted.id == "x"
    assert redacted.sensitivity == Sensitivity.confidential


def test_mask_hook_updates_token_estimate() -> None:
    hook = MaskRedactionHook()
    item = _item("x", Sensitivity.restricted, "very long text " * 100)
    redacted = hook.redact(item)
    assert redacted.token_estimate == len("[REDACTED: restricted]") // 4


# ------------------------------------------------------------------
# Edge cases
# ------------------------------------------------------------------


def test_empty_items_list() -> None:
    policy = ContextPolicy(sensitivity_floor=Sensitivity.confidential)
    filtered, dropped = apply_sensitivity_filter([], policy)
    assert filtered == []
    assert dropped == 0


def test_all_items_dropped() -> None:
    policy = ContextPolicy(sensitivity_floor=Sensitivity.public)
    items = [_item("a", Sensitivity.public), _item("b", Sensitivity.restricted)]
    filtered, dropped = apply_sensitivity_filter(items, policy)
    assert dropped == 2
    assert filtered == []


def test_unknown_hook_name_raises() -> None:
    policy = ContextPolicy(
        sensitivity_floor=Sensitivity.confidential,
        sensitivity_action="redact",
        redaction_hooks=["nonexistent"],
    )
    item = _item("x", Sensitivity.confidential)
    with pytest.raises(ValueError, match="Unknown redaction hook"):
        apply_sensitivity_filter([item], policy)


def test_unknown_sensitivity_action_raises() -> None:
    policy = ContextPolicy(
        sensitivity_floor=Sensitivity.confidential,
        sensitivity_action="dorp",
    )
    item = _item("x", Sensitivity.confidential)
    with pytest.raises(ValueError, match="Unknown sensitivity_action"):
        apply_sensitivity_filter([item], policy)


# ------------------------------------------------------------------
# ContextItem serde roundtrip with sensitivity
# ------------------------------------------------------------------


def test_context_item_to_dict_includes_sensitivity() -> None:
    item = _item("s1", Sensitivity.restricted, "secret")
    d = item.to_dict()
    assert d["sensitivity"] == "restricted"


def test_context_item_from_dict_reads_sensitivity() -> None:
    data = {
        "id": "s1",
        "kind": "user_turn",
        "text": "hello",
        "sensitivity": "confidential",
    }
    item = ContextItem.from_dict(data)
    assert item.sensitivity == Sensitivity.confidential


def test_context_item_from_dict_defaults_to_public() -> None:
    data = {"id": "s2", "kind": "user_turn", "text": "hello"}
    item = ContextItem.from_dict(data)
    assert item.sensitivity == Sensitivity.public


def test_context_item_roundtrip() -> None:
    original = _item("rt", Sensitivity.internal, "internal data")
    rebuilt = ContextItem.from_dict(original.to_dict())
    assert rebuilt.sensitivity == Sensitivity.internal
    assert rebuilt.id == original.id
    assert rebuilt.text == original.text


# ------------------------------------------------------------------
# Integration: manager build excludes sensitive content from prompt
# ------------------------------------------------------------------


def test_build_excludes_restricted_from_prompt() -> None:
    log = InMemoryEventLog()
    log.append(_item("u1", Sensitivity.public, "safe question"))
    log.append(
        ContextItem(
            id="secret1",
            kind=ItemKind.doc_snippet,
            text="SSN: 123-45-6789",
            sensitivity=Sensitivity.restricted,
        )
    )
    policy = ContextPolicy(sensitivity_floor=Sensitivity.confidential)
    mgr = ContextManager(event_log=log, policy=policy)
    pack = mgr.build_sync(phase=Phase.answer, query="hello")
    assert "SSN" not in pack.prompt
    assert "safe question" in pack.prompt


def test_build_redact_mode_masks_in_prompt() -> None:
    log = InMemoryEventLog()
    log.append(_item("u1", Sensitivity.public, "safe question"))
    log.append(
        ContextItem(
            id="secret1",
            kind=ItemKind.doc_snippet,
            text="SSN: 123-45-6789",
            sensitivity=Sensitivity.restricted,
        )
    )
    policy = ContextPolicy(
        sensitivity_floor=Sensitivity.confidential,
        sensitivity_action="redact",
    )
    mgr = ContextManager(event_log=log, policy=policy)
    pack = mgr.build_sync(phase=Phase.answer, query="hello")
    assert "SSN" not in pack.prompt
    assert "[REDACTED: restricted]" in pack.prompt
    assert "safe question" in pack.prompt
