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

import logging
from dataclasses import replace

from contextweaver.config import SENSITIVITY_ACTIONS, ContextPolicy
from contextweaver.exceptions import ConfigError, ItemNotFoundError
from contextweaver.protocols import RedactionHook, TokenEstimator
from contextweaver.tokens import heuristic_counter
from contextweaver.types import ContextItem, Sensitivity

logger = logging.getLogger("contextweaver.context")

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
        ConfigError: If *name* is already registered.  A duplicate registration
            is a configuration mistake, not a policy violation (issue #463);
            use :func:`unregister_redaction_hook` first to replace a hook.
    """
    if name in _HOOK_REGISTRY:
        msg = f"Redaction hook {name!r} is already registered"
        raise ConfigError(msg)
    _HOOK_REGISTRY[name] = hook


def unregister_redaction_hook(name: str) -> None:
    """Remove a previously registered :class:`RedactionHook` (issue #463).

    Provided for test hygiene and long-lived processes that need to replace a
    hook: ``unregister_redaction_hook(name)`` then re-:func:`register_redaction_hook`.

    Args:
        name: The hook name to remove.

    Raises:
        ItemNotFoundError: If no hook is registered under *name*.
    """
    if name not in _HOOK_REGISTRY:
        raise ItemNotFoundError(f"Redaction hook {name!r} is not registered")
    del _HOOK_REGISTRY[name]


class MaskRedactionHook:
    """Replace item text with ``[REDACTED: {sensitivity}]``.

    Structural fields (id, kind, parent_id) are preserved so the item still
    participates in dependency closure and rendering structure, and
    ``metadata["redacted"]`` is set to ``True`` so downstream code can recognise
    a redacted item.

    The item's ``artifact_ref`` is **dropped** (issue #451): if it were kept, the
    rendered prompt would advertise an artifact handle and the ``drilldown`` path
    could re-fetch the original, pre-redaction bytes and re-inject them — making
    ``redact`` weaker than its name implies.  Dropping the ref makes redaction
    effective end-to-end on the standard rendered surfaces.

    The placeholder's ``token_estimate`` is computed through a configured
    :class:`~contextweaver.protocols.TokenEstimator` rather than an inline
    ``len // 4`` literal, so every number that feeds budget enforcement comes
    from one source of truth (issue #530). For the ASCII redaction placeholder
    the default script-aware heuristic yields exactly ``len // 4``, so the
    estimate is unchanged from prior behaviour.

    Args:
        estimator: Token estimator used to size the placeholder. ``None`` uses
            the canonical :func:`contextweaver.tokens.heuristic_counter`; the
            build pipeline threads the manager's configured estimator so a
            custom counter is honoured on redaction paths.
    """

    def __init__(self, estimator: TokenEstimator | None = None) -> None:
        self._estimator: TokenEstimator = estimator or heuristic_counter()

    def redact(self, item: ContextItem) -> ContextItem:
        """Return a copy of *item* with its text replaced by a redaction mask.

        Args:
            item: The context item to redact.

        Returns:
            A new :class:`ContextItem` with masked text, a token estimate from
            the configured estimator, ``artifact_ref`` cleared, and
            ``metadata["redacted"]`` set (issue #451).
        """
        placeholder = f"[REDACTED: {item.sensitivity.value}]"
        metadata = dict(item.metadata)
        metadata["redacted"] = True
        return replace(
            item,
            text=placeholder,
            token_estimate=self._estimator.estimate(placeholder),
            artifact_ref=None,
            metadata=metadata,
        )


# Register the built-in hook so it can be referenced by name in
# ContextPolicy.redaction_hooks.
_HOOK_REGISTRY["mask"] = MaskRedactionHook()


def _resolve_hooks(
    names: list[str],
    estimator: TokenEstimator | None = None,
) -> list[RedactionHook]:
    """Resolve hook names to instances.

    The built-in ``"mask"`` hook is instantiated per-call so the configured
    *estimator* sizes its placeholder; custom registered hooks are returned
    as-is (they own their own behaviour).

    Args:
        names: Hook names from :attr:`ContextPolicy.redaction_hooks`.
        estimator: Token estimator threaded into the built-in mask hook.

    Returns:
        Resolved :class:`RedactionHook` instances.

    Raises:
        ConfigError: If a name cannot be resolved.
    """
    hooks: list[RedactionHook] = []
    for name in names:
        if name == "mask":
            hooks.append(MaskRedactionHook(estimator=estimator))
            continue
        hook = _HOOK_REGISTRY.get(name)
        if hook is None:
            msg = f"Unknown redaction hook {name!r}. Available: {sorted(_HOOK_REGISTRY)}"
            raise ConfigError(msg)
        hooks.append(hook)
    return hooks


def apply_sensitivity_filter(
    items: list[ContextItem],
    policy: ContextPolicy,
    estimator: TokenEstimator | None = None,
) -> tuple[list[ContextItem], int]:
    """Filter or redact items whose sensitivity meets or exceeds the policy floor.

    Args:
        items: Candidate items to inspect.
        policy: The active context policy (provides ``sensitivity_floor``,
            ``sensitivity_action``, and ``redaction_hooks``).
        estimator: Optional token estimator used to size redaction
            placeholders (issue #530). ``None`` uses the canonical
            script-aware heuristic; the build pipeline passes the manager's
            configured estimator so a custom counter is honoured here too.

    Returns:
        A 2-tuple ``(filtered_items, dropped_count)``.  In ``"redact"`` mode
        *dropped_count* is always ``0`` because items are kept (with masked
        text).
    """
    floor_level = _SENSITIVITY_ORDER[policy.sensitivity_floor]
    action = policy.sensitivity_action

    if action not in SENSITIVITY_ACTIONS:
        msg = f"Unknown sensitivity_action {action!r}. Valid: {sorted(SENSITIVITY_ACTIONS)}"
        raise ConfigError(msg)

    # Resolve redaction hooks once (only needed in redact mode).
    hooks: list[RedactionHook] = []
    if action == "redact":
        hook_names = policy.redaction_hooks or ["mask"]
        hooks = _resolve_hooks(hook_names, estimator)

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

    logger.debug(
        "sensitivity_filter: action=%s, floor=%s, passed=%d, dropped=%d",
        action,
        policy.sensitivity_floor.value,
        len(result),
        dropped,
    )
    return result, dropped
