"""Sensitivity enforcement for the contextweaver Context Engine.

Filters or redacts :class:`~contextweaver.types.ContextItem` objects whose
sensitivity level meets or exceeds the configured
:attr:`~contextweaver.config.ContextPolicy.sensitivity_floor`.

Two actions are supported:

* **drop** (default) — the item is silently removed from the candidate list.
* **redact** — the item's text is replaced with a placeholder via a
  :class:`~contextweaver.protocols.RedactionHook`.

The built-in :class:`MaskRedactionHook` replaces the text with
``[REDACTED: {sensitivity}]`` while preserving all other item metadata.
"""

from __future__ import annotations

from dataclasses import replace

from contextweaver.config import ContextPolicy
from contextweaver.protocols import RedactionHook
from contextweaver.types import ContextItem, Sensitivity

# Ordered severity levels for comparison.
_SENSITIVITY_ORDER: dict[Sensitivity, int] = {
    Sensitivity.public: 0,
    Sensitivity.internal: 1,
    Sensitivity.confidential: 2,
    Sensitivity.restricted: 3,
}

# Hook registry (name → instance).  Built-in hooks are registered at
# module load time; users can add their own via :func:`register_redaction_hook`.
_HOOK_REGISTRY: dict[str, RedactionHook] = {}


def register_redaction_hook(name: str, hook: RedactionHook) -> None:
    """Register a custom :class:`~contextweaver.protocols.RedactionHook`.

    Once registered, the *name* can be used in
    :attr:`~contextweaver.config.ContextPolicy.redaction_hooks` just like the
    built-in ``"mask"`` hook.

    Args:
        name: Short identifier for the hook (e.g. ``"my_custom_hook"``).
        hook: An object implementing the :class:`RedactionHook` protocol.

    Raises:
        ValueError: If *name* is already registered.
    """
    if name in _HOOK_REGISTRY:
        msg = f"Redaction hook {name!r} is already registered"
        raise ValueError(msg)
    _HOOK_REGISTRY[name] = hook


class MaskRedactionHook:
    """Replace item text with ``[REDACTED: {sensitivity}]``.

    All other item fields (id, kind, metadata, parent_id, artifact_ref) are
    preserved so the item still participates in dependency closure, stats
    tracking, and rendering structure.
    """

    def redact(self, item: ContextItem) -> ContextItem:
        """Return a copy of *item* with its text replaced by a redaction mask.

        Args:
            item: The context item to redact.

        Returns:
            A new :class:`ContextItem` with masked text and a minimal
            token estimate.
        """
        placeholder = f"[REDACTED: {item.sensitivity.value}]"
        return replace(item, text=placeholder, token_estimate=len(placeholder) // 4)


# Register the built-in hook so it can be referenced by name in
# ContextPolicy.redaction_hooks.
_HOOK_REGISTRY["mask"] = MaskRedactionHook()


def _resolve_hooks(names: list[str]) -> list[RedactionHook]:
    """Resolve hook names to instances.

    Args:
        names: Hook names from :attr:`ContextPolicy.redaction_hooks`.

    Returns:
        Resolved :class:`RedactionHook` instances.

    Raises:
        ValueError: If a name cannot be resolved.
    """
    hooks: list[RedactionHook] = []
    for name in names:
        hook = _HOOK_REGISTRY.get(name)
        if hook is None:
            msg = f"Unknown redaction hook {name!r}. Available: {sorted(_HOOK_REGISTRY)}"
            raise ValueError(msg)
        hooks.append(hook)
    return hooks


def apply_sensitivity_filter(
    items: list[ContextItem],
    policy: ContextPolicy,
) -> tuple[list[ContextItem], int]:
    """Filter or redact items whose sensitivity meets or exceeds the policy floor.

    Args:
        items: Candidate items to inspect.
        policy: The active context policy (provides ``sensitivity_floor``,
            ``sensitivity_action``, and ``redaction_hooks``).

    Returns:
        A 2-tuple ``(filtered_items, dropped_count)``.  In ``"redact"`` mode
        *dropped_count* is always ``0`` because items are kept (with masked
        text).
    """
    floor_level = _SENSITIVITY_ORDER[policy.sensitivity_floor]
    action = policy.sensitivity_action

    _VALID_ACTIONS = {"drop", "redact"}
    if action not in _VALID_ACTIONS:
        msg = f"Unknown sensitivity_action {action!r}. Valid: {sorted(_VALID_ACTIONS)}"
        raise ValueError(msg)

    # Resolve redaction hooks once (only needed in redact mode).
    hooks: list[RedactionHook] = []
    if action == "redact":
        hook_names = policy.redaction_hooks or ["mask"]
        hooks = _resolve_hooks(hook_names)

    result: list[ContextItem] = []
    dropped = 0
    for item in items:
        item_level = _SENSITIVITY_ORDER[item.sensitivity]
        if item_level >= floor_level:
            if action == "redact":
                redacted = item
                for hook in hooks:
                    redacted = hook.redact(redacted)
                result.append(redacted)
            else:
                # Default: drop
                dropped += 1
        else:
            result.append(item)

    return result, dropped
