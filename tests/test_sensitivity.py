"""Tests for contextweaver.context.sensitivity."""

from __future__ import annotations

import pytest

from contextweaver.config import ContextPolicy
from contextweaver.context.manager import ContextManager
from contextweaver.context.sensitivity import (
    _HOOK_REGISTRY,
    _SENSITIVITY_ORDER,
    MaskRedactionHook,
    apply_sensitivity_filter,
    register_redaction_hook,
    unregister_redaction_hook,
)
from contextweaver.exceptions import ConfigError, ItemNotFoundError
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


def test_redact_mode_preserves_structure_and_marks_redacted() -> None:
    """Redaction preserves structural fields, keeps original metadata keys, and
    adds the ``redacted`` marker while dropping the artifact handle (issue #451)."""
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
    # Original metadata is preserved; the redaction marker is added (issue #451).
    assert redacted.metadata == {"source": "vault", "redacted": True}
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
    # Default counter is the script-aware heuristic; for the ASCII placeholder
    # it equals len // 4, so the estimate is unchanged from prior behaviour.
    assert redacted.token_estimate == len("[REDACTED: restricted]") // 4


def test_mask_hook_honours_configured_estimator() -> None:
    """The redaction placeholder estimate comes from the configured counter (#530)."""

    class _Const:
        name = "const-7"

        def estimate(self, text: str) -> int:
            return 7

    hook = MaskRedactionHook(estimator=_Const())
    redacted = hook.redact(_item("x", Sensitivity.restricted, "secret"))
    assert redacted.text == "[REDACTED: restricted]"
    assert redacted.token_estimate == 7


def test_redaction_path_uses_manager_estimator() -> None:
    """A custom estimator passed to the manager is honoured on redaction paths (#530)."""

    class _Const:
        name = "const-99"

        def estimate(self, text: str) -> int:
            return 99

    policy = ContextPolicy(sensitivity_floor=Sensitivity.confidential, sensitivity_action="redact")
    filtered, dropped = apply_sensitivity_filter(
        [_item("a", Sensitivity.restricted, "top secret payload")],
        policy,
        estimator=_Const(),
    )
    assert dropped == 0
    assert len(filtered) == 1
    assert filtered[0].text == "[REDACTED: restricted]"
    assert filtered[0].token_estimate == 99


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
    with pytest.raises(ConfigError, match="Unknown redaction hook"):
        apply_sensitivity_filter([item], policy)


def test_unknown_sensitivity_action_raises_at_construction() -> None:
    """An invalid action is rejected when the policy is built (issue #463)."""
    with pytest.raises(ConfigError, match="sensitivity_action must be one of"):
        ContextPolicy(
            sensitivity_floor=Sensitivity.confidential,
            sensitivity_action="dorp",  # type: ignore[arg-type]
        )


def test_sensitivity_filter_guards_post_construction_mutation() -> None:
    """The runtime filter still rejects an action mutated after construction."""
    policy = ContextPolicy(sensitivity_floor=Sensitivity.confidential)
    policy.sensitivity_action = "dorp"  # type: ignore[assignment]
    item = _item("x", Sensitivity.confidential)
    with pytest.raises(ConfigError, match="Unknown sensitivity_action"):
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


# ------------------------------------------------------------------
# register_redaction_hook
# ------------------------------------------------------------------


def test_register_custom_hook_and_use_in_redact_mode() -> None:
    """A user-registered hook can be referenced by name in ContextPolicy."""

    class UppercaseHook:
        def redact(self, item: ContextItem) -> ContextItem:
            from dataclasses import replace as _replace

            return _replace(item, text=item.text.upper())

    # Register & use
    register_redaction_hook("upper_test", UppercaseHook())
    try:
        policy = ContextPolicy(
            sensitivity_floor=Sensitivity.confidential,
            sensitivity_action="redact",
            redaction_hooks=["upper_test"],
        )
        items = [_item("a", Sensitivity.restricted, text="secret data")]
        result, dropped = apply_sensitivity_filter(items, policy)
        assert dropped == 0
        assert len(result) == 1
        assert result[0].text == "SECRET DATA"
    finally:
        # Clean up so other tests are not affected.
        del _HOOK_REGISTRY["upper_test"]


def test_register_duplicate_hook_raises() -> None:
    """Registering a hook with an existing name raises ConfigError (issue #463)."""
    with pytest.raises(ConfigError, match="already registered"):
        register_redaction_hook("mask", MaskRedactionHook())


def test_unregister_redaction_hook_roundtrip() -> None:
    """A hook can be unregistered and re-registered (test hygiene, issue #463)."""
    register_redaction_hook("temp_hook", MaskRedactionHook())
    unregister_redaction_hook("temp_hook")
    # Re-registering under the same name now succeeds (no lingering entry).
    register_redaction_hook("temp_hook", MaskRedactionHook())
    unregister_redaction_hook("temp_hook")


def test_unregister_unknown_hook_raises() -> None:
    """Unregistering a name that was never registered raises ItemNotFoundError."""
    with pytest.raises(ItemNotFoundError, match="not registered"):
        unregister_redaction_hook("never_registered_hook")


# ------------------------------------------------------------------
# Redaction is effective end-to-end (issue #451)
# ------------------------------------------------------------------


def test_redaction_drops_artifact_ref() -> None:
    """A redacted item must not retain an artifact handle that the prompt would
    advertise and drilldown could dereference back to the original (issue #451)."""
    from contextweaver.types import ArtifactRef

    policy = ContextPolicy(
        sensitivity_floor=Sensitivity.confidential,
        sensitivity_action="redact",
    )
    item = ContextItem(
        id="r1",
        kind=ItemKind.tool_result,
        text="secret payload",
        sensitivity=Sensitivity.restricted,
        artifact_ref=ArtifactRef(handle="artifact:r1", media_type="text/plain", size_bytes=14),
    )
    filtered, _ = apply_sensitivity_filter([item], policy)
    assert filtered[0].text == "[REDACTED: restricted]"
    assert filtered[0].artifact_ref is None
    assert filtered[0].metadata.get("redacted") is True


def test_redacted_item_renders_without_handle() -> None:
    """The rendered prompt for a redacted item exposes no artifact handle (issue #451)."""
    from contextweaver.context.prompt import render_item
    from contextweaver.types import ArtifactRef

    policy = ContextPolicy(
        sensitivity_floor=Sensitivity.confidential,
        sensitivity_action="redact",
    )
    item = ContextItem(
        id="r2",
        kind=ItemKind.tool_result,
        text="AKIAIOSFODNN7EXAMPLE",
        sensitivity=Sensitivity.restricted,
        artifact_ref=ArtifactRef(handle="artifact:r2", media_type="text/plain", size_bytes=20),
    )
    filtered, _ = apply_sensitivity_filter([item], policy)
    rendered = render_item(filtered[0])
    assert "artifact:r2" not in rendered
    assert "AKIAIOSFODNN7EXAMPLE" not in rendered


# ------------------------------------------------------------------
# SecretRedactor hook (issue #428)
# ------------------------------------------------------------------


def test_secret_redactor_registered_under_name() -> None:
    """Importing the package registers the built-in "secret" hook (issue #428)."""
    import contextweaver  # noqa: F401  (ensures registration side effect)

    assert "secret" in _HOOK_REGISTRY


def test_secret_redactor_scrubs_substring_keeps_rest() -> None:
    from contextweaver.context.secret_redaction import SecretRedactor

    item = ContextItem(
        id="s1",
        kind=ItemKind.tool_result,
        text="connecting with key=AKIAIOSFODNN7EXAMPLE now",
        sensitivity=Sensitivity.public,
    )
    redacted = SecretRedactor().redact(item)
    assert "AKIAIOSFODNN7EXAMPLE" not in redacted.text
    assert redacted.text.startswith("connecting with key=")
    assert redacted.text.endswith(" now")


def test_secret_redactor_noop_when_clean_returns_same_item() -> None:
    from contextweaver.context.secret_redaction import SecretRedactor

    item = ContextItem(id="c1", kind=ItemKind.tool_result, text="no secrets here")
    assert SecretRedactor().redact(item) is item


def test_secret_hook_via_policy() -> None:
    """The "secret" hook works through ContextPolicy.redaction_hooks (issue #428)."""
    import contextweaver  # noqa: F401

    policy = ContextPolicy(
        sensitivity_floor=Sensitivity.confidential,
        sensitivity_action="redact",
        redaction_hooks=["secret"],
    )
    item = ContextItem(
        id="h1",
        kind=ItemKind.tool_result,
        text="db: postgres://u:p4ssword@host/db",
        sensitivity=Sensitivity.confidential,
    )
    filtered, dropped = apply_sensitivity_filter([item], policy)
    assert dropped == 0
    assert "p4ssword" not in filtered[0].text
