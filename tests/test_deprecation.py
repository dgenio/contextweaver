"""Tests for the runtime deprecation machinery (issue #517)."""

from __future__ import annotations

import warnings
from collections.abc import Iterator

import pytest

import contextweaver._deprecation as _deprecation
from contextweaver._deprecation import (
    DEPRECATION_MESSAGE_PREFIX,
    Deprecation,
    active_deprecations,
    deprecated,
    register_deprecation,
    warn_deprecated,
)
from contextweaver.exceptions import ConfigError


@pytest.fixture(autouse=True)
def _isolate_registry() -> Iterator[None]:
    """Snapshot and restore the global deprecation registry around each test.

    These tests register throwaway ``t.*`` deprecations; without isolation they
    leak into the process-global ``_REGISTRY`` and would make any later
    assertion on the exact contents of ``active_deprecations()`` (e.g. the
    docs-drift guard) order-dependent.
    """
    snapshot = dict(_deprecation._REGISTRY)
    try:
        yield
    finally:
        _deprecation._REGISTRY.clear()
        _deprecation._REGISTRY.update(snapshot)


def test_deprecation_message_is_actionable() -> None:
    dep = Deprecation(name="Foo", since="0.16.0", removal="1.0.0", instead="Bar")
    msg = dep.message()
    assert msg.startswith(DEPRECATION_MESSAGE_PREFIX)
    assert "Foo is deprecated since contextweaver 0.16.0" in msg
    assert "scheduled for removal in 1.0.0" in msg
    assert "Use Bar instead." in msg


def test_warn_deprecated_registers_and_warns() -> None:
    register_deprecation("t.warn_once", since="0.16.0", removal="1.0.0", instead="new")
    with pytest.warns(DeprecationWarning, match="t.warn_once is deprecated") as record:
        warn_deprecated("t.warn_once")
    assert record[0].category is DeprecationWarning


def test_warn_deprecated_dedups_per_call_site_under_default_filter() -> None:
    register_deprecation("t.warn_dedup", since="0.16.0", removal="1.0.0", instead="new")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("default")
        for _ in range(3):
            warn_deprecated("t.warn_dedup")  # same call site → one user-visible warning
    matched = [w for w in caught if "t.warn_dedup is deprecated" in str(w.message)]
    assert len(matched) == 1


def test_warn_deprecated_inline_registration() -> None:
    with pytest.warns(DeprecationWarning, match="Use replacement instead"):
        warn_deprecated("t.inline", since="0.16.0", removal="1.0.0", instead="replacement")
    assert any(d.name == "t.inline" for d in active_deprecations())


def test_warn_deprecated_unregistered_raises() -> None:
    with pytest.raises(KeyError, match="not registered"):
        warn_deprecated("t.never_registered")


def test_register_deprecation_is_idempotent_but_rejects_conflicts() -> None:
    first = register_deprecation("t.dup", since="0.16.0", removal="1.0.0", instead="x")
    again = register_deprecation("t.dup", since="0.16.0", removal="1.0.0", instead="x")
    assert first == again
    with pytest.raises(ConfigError, match="conflicting deprecation"):
        register_deprecation("t.dup", since="0.17.0", removal="1.0.0", instead="x")


def test_deprecated_decorator_warns_and_preserves_behaviour() -> None:
    @deprecated("t.old_fn", since="0.16.0", removal="1.0.0", instead="new_fn")
    def old_fn(value: int) -> int:
        return value * 2

    with pytest.warns(DeprecationWarning, match="t.old_fn is deprecated"):
        result = old_fn(21)
    assert result == 42
    assert old_fn.__name__ == "old_fn"
    assert any(d.name == "t.old_fn" for d in active_deprecations())


def test_active_deprecations_is_sorted_and_immutable() -> None:
    deps = active_deprecations()
    assert isinstance(deps, tuple)
    names = [d.name for d in deps]
    assert names == sorted(names)


def test_non_deprecated_path_emits_no_warning() -> None:
    def healthy() -> int:
        return 1

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert healthy() == 1
