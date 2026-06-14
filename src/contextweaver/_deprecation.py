"""Runtime deprecation machinery for contextweaver (issue #517).

The stability policy in ``docs/stability.md`` promises that "deprecated public
APIs warn before removal", but the codebase had no way to deliver a runtime
warning.  This module is that mechanism: a thin, dependency-free layer over the
standard :mod:`warnings` module that

* emits :class:`DeprecationWarning`\\s with consistent, actionable wording
  ("deprecated since X, removal in Y, use Z instead"),
* records every active deprecation in one registry so docs, the upgrade guide,
  and the CHANGELOG can be checked against a single source of truth, and
* honours the once-per-call-site default of the warnings module (correct
  ``stacklevel``) so end users are not spammed.

Every message starts with :data:`DEPRECATION_MESSAGE_PREFIX` so CI can escalate
*contextweaver's own* deprecations to errors (see ``pyproject.toml``
``filterwarnings``) without touching the unrelated ``DeprecationWarning``\\s
that third-party dependencies emit.

Public surface is intentionally module-private (``_deprecation``): it is an
internal authoring tool, not a stable API for downstream code.
"""

from __future__ import annotations

import functools
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar, cast

from contextweaver.exceptions import ConfigError

__all__ = [
    "DEPRECATION_MESSAGE_PREFIX",
    "Deprecation",
    "active_deprecations",
    "deprecated",
    "register_deprecation",
    "warn_deprecated",
]

#: Stable prefix on every contextweaver deprecation message.  CI escalates
#: only warnings starting with this token (``pyproject.toml`` ``filterwarnings``),
#: so first-party deprecations fail tests while third-party ones do not.
DEPRECATION_MESSAGE_PREFIX = "contextweaver deprecation:"

_F = TypeVar("_F", bound=Callable[..., Any])


@dataclass(frozen=True)
class Deprecation:
    """A single, documented deprecation of a public surface.

    Attributes:
        name: The deprecated surface, e.g. ``"ToolCard"`` or
            ``"RouteResult.debug_trace"``.
        since: Version the deprecation was announced in (e.g. ``"0.16.0"``).
        removal: Version the surface is scheduled for removal in
            (e.g. ``"1.0.0"``).
        instead: The canonical replacement to point users at.
    """

    name: str
    since: str
    removal: str
    instead: str

    def message(self) -> str:
        """Render the actionable warning / docs message for this deprecation."""
        return (
            f"{DEPRECATION_MESSAGE_PREFIX} {self.name} is deprecated since "
            f"contextweaver {self.since} and is scheduled for removal in "
            f"{self.removal}. Use {self.instead} instead."
        )


# Single source of truth for active deprecations, keyed by ``name``.
_REGISTRY: dict[str, Deprecation] = {}

# Canonical inventory of the pre-1.0 legacy compatibility shims (issue #642).
# Registered eagerly at import so ``active_deprecations()`` reflects the full
# set regardless of which shims have been exercised, and so docs / the upgrade
# guide can be checked against one source.  The shim call sites import only
# :func:`warn_deprecated` (and :func:`deprecated`) and refer to these by name.
_SHIMS: tuple[Deprecation, ...] = (
    Deprecation("ToolCard", since="0.16.0", removal="1.0.0", instead="SelectableItem"),
    Deprecation(
        "RouteResult.debug_trace", since="0.16.0", removal="1.0.0", instead="RouteResult.trace"
    ),
    Deprecation(
        "RouteTrace.to_legacy_dicts",
        since="0.16.0",
        removal="1.0.0",
        instead="the structured RouteTrace fields (steps / to_dict)",
    ),
    Deprecation(
        "Router(scorer=...)",
        since="0.16.0",
        removal="1.0.0",
        instead="retriever= (a Retriever) or scorer_backend=",
    ),
)
for _shim in _SHIMS:
    _REGISTRY[_shim.name] = _shim


def register_deprecation(name: str, *, since: str, removal: str, instead: str) -> Deprecation:
    """Record an active deprecation in the registry and return it.

    Idempotent: re-registering the same ``name`` with identical fields returns
    the existing entry; conflicting re-registration raises
    :class:`~contextweaver.exceptions.ConfigError` so two shims cannot quietly
    disagree about the same name.
    """
    candidate = Deprecation(name=name, since=since, removal=removal, instead=instead)
    existing = _REGISTRY.get(name)
    if existing is not None and existing != candidate:
        raise ConfigError(
            f"conflicting deprecation registration for {name!r}: {existing} != {candidate}"
        )
    _REGISTRY[name] = candidate
    return candidate


def active_deprecations() -> tuple[Deprecation, ...]:
    """Return all registered deprecations, sorted by name (deterministic)."""
    return tuple(_REGISTRY[name] for name in sorted(_REGISTRY))


def warn_deprecated(
    name: str,
    *,
    since: str | None = None,
    removal: str | None = None,
    instead: str | None = None,
    stacklevel: int = 2,
) -> None:
    """Emit a :class:`DeprecationWarning` for ``name``.

    If ``since`` / ``removal`` / ``instead`` are given the deprecation is
    registered (if not already); otherwise ``name`` must already be in the
    registry.  ``stacklevel`` defaults to ``2`` so the warning points at the
    caller's call site, giving the once-per-site dedup the warnings module
    provides under its default filters.
    """
    if since is not None and removal is not None and instead is not None:
        deprecation = register_deprecation(name, since=since, removal=removal, instead=instead)
    else:
        deprecation = _REGISTRY.get(name) or _missing(name)
    warnings.warn(deprecation.message(), DeprecationWarning, stacklevel=stacklevel + 1)


def _missing(name: str) -> Deprecation:
    raise KeyError(
        f"deprecation {name!r} is not registered; pass since=/removal=/instead= "
        f"to warn_deprecated or call register_deprecation first"
    )


def deprecated(
    name: str | None = None,
    *,
    since: str,
    removal: str,
    instead: str,
) -> Callable[[_F], _F]:
    """Decorate a callable so calling it emits a deprecation warning.

    The wrapped callable's behaviour is otherwise unchanged.  ``name`` defaults
    to the callable's qualified name.  Works on plain functions and methods.

    Example:
        >>> @deprecated(since="0.16.0", removal="1.0.0", instead="new_api")
        ... def old_api() -> int:
        ...     return 1
    """

    def decorate(func: _F) -> _F:
        resolved: str = name or str(getattr(func, "__qualname__", "callable"))
        register_deprecation(resolved, since=since, removal=removal, instead=instead)

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401 — wraps an arbitrary callable
            warn_deprecated(resolved, stacklevel=2)
            return func(*args, **kwargs)

        return cast(_F, wrapper)

    return decorate
