"""Tests for contextweaver.exceptions."""

from __future__ import annotations

import inspect

import pytest

from contextweaver import exceptions as exc_mod
from contextweaver.envelope import BuildStats
from contextweaver.exceptions import (
    ArtifactNotFoundError,
    BudgetExceededError,
    BudgetOverflowError,
    CatalogError,
    ConfigError,
    ContextWeaverError,
    DuplicateItemError,
    GraphBuildError,
    ItemNotFoundError,
    PolicyViolationError,
    RouteError,
)

# Frozen, machine-readable error codes (issue #635).  This golden list is the
# stability contract: changing a code, or adding/removing an exception without
# updating this map, must fail CI.  Codes are part of the public compatibility
# surface — see docs/errors.md for the per-code causes and remedies (#637).
GOLDEN_CODES: dict[str, str] = {
    "ContextWeaverError": "CW_ERROR",
    "BudgetExceededError": "CW_BUDGET_EXCEEDED",
    "BudgetOverflowError": "CW_BUDGET_OVERFLOW",
    "ArtifactNotFoundError": "CW_ARTIFACT_NOT_FOUND",
    "ArtifactStoreQuotaError": "CW_ARTIFACT_STORE_QUOTA",
    "PolicyViolationError": "CW_POLICY_VIOLATION",
    "ItemNotFoundError": "CW_ITEM_NOT_FOUND",
    "GraphBuildError": "CW_GRAPH_BUILD",
    "RouteError": "CW_ROUTE",
    "CatalogError": "CW_CATALOG",
    "CatalogValidationError": "CW_CATALOG_VALIDATION",
    "DuplicateItemError": "CW_DUPLICATE_ITEM",
    "ConfigError": "CW_CONFIG",
    "ValidationError": "CW_VALIDATION",
    "DeterminismError": "CW_DETERMINISM",
    "PathInvalidError": "CW_PATH_INVALID",
    "PathNotFoundError": "CW_PATH_NOT_FOUND",
    "UpstreamError": "CW_UPSTREAM",
    "StoreClosedError": "CW_STORE_CLOSED",
    "UpstreamStartupError": "CW_UPSTREAM_STARTUP",
}


def _module_exception_classes() -> dict[str, type[ContextWeaverError]]:
    """Every ``ContextWeaverError`` subclass *defined in* the exceptions module."""
    return {
        name: cls
        for name, cls in inspect.getmembers(exc_mod, inspect.isclass)
        if issubclass(cls, ContextWeaverError) and cls.__module__ == exc_mod.__name__
    }


@pytest.mark.parametrize(
    "exc_cls",
    [
        BudgetExceededError,
        ArtifactNotFoundError,
        PolicyViolationError,
        ItemNotFoundError,
        GraphBuildError,
        RouteError,
        CatalogError,
        DuplicateItemError,
    ],
)
def test_all_exceptions_inherit_from_base(exc_cls: type[ContextWeaverError]) -> None:
    err = exc_cls("test message")
    assert isinstance(err, ContextWeaverError)
    assert isinstance(err, Exception)
    # str() now carries the stable code prefix (#635); the message is preserved.
    assert str(err).startswith(f"[{exc_cls.code}] ")
    assert "test message" in str(err)


def test_base_exception_catchall() -> None:
    with pytest.raises(ContextWeaverError):
        raise ItemNotFoundError("missing")


def test_specific_catch() -> None:
    with pytest.raises(ArtifactNotFoundError):
        raise ArtifactNotFoundError("handle-xyz")


def test_budget_overflow_error_carries_stats_and_kinds() -> None:
    """BudgetOverflowError attaches the would-be stats + dropped kinds (#510)."""
    stats = BuildStats(dropped_count=2)
    err = BudgetOverflowError("overflowed", stats=stats, dropped_kinds=["policy"])
    assert isinstance(err, ContextWeaverError)
    assert err.stats is stats
    assert err.dropped_kinds == ["policy"]


def test_budget_overflow_error_defaults_empty_kinds() -> None:
    err = BudgetOverflowError("overflowed", stats=BuildStats())
    assert err.dropped_kinds == []


def test_budget_overflow_error_normalizes_dropped_kinds() -> None:
    """dropped_kinds is stored sorted + de-duplicated, per its docstring (#510)."""
    err = BudgetOverflowError(
        "overflowed", stats=BuildStats(), dropped_kinds=["policy", "doc_snippet", "policy"]
    )
    assert err.dropped_kinds == ["doc_snippet", "policy"]


# --- stable error codes + hints (issue #635) -------------------------------


def test_codes_match_golden_list() -> None:
    """Codes are frozen: the module's classes must match GOLDEN_CODES exactly."""
    discovered = {name: cls.code for name, cls in _module_exception_classes().items()}
    assert discovered == GOLDEN_CODES


def test_every_exception_has_a_nonempty_code() -> None:
    for name, cls in _module_exception_classes().items():
        assert isinstance(cls.code, str) and cls.code, f"{name} is missing a code"


def test_codes_are_unique() -> None:
    codes = [cls.code for cls in _module_exception_classes().values()]
    assert len(codes) == len(set(codes)), "duplicate error codes detected"


def test_str_includes_code_and_message() -> None:
    assert str(RouteError("no route")) == "[CW_ROUTE] no route"


def test_str_with_no_message_is_just_the_code() -> None:
    assert str(ContextWeaverError()) == "[CW_ERROR]"


def test_explicit_hint_overrides_default() -> None:
    err = ConfigError("bad preset", hint="do X instead")
    assert err.hint == "do X instead"
    assert str(err) == "[CW_CONFIG] bad preset (hint: do X instead)"


def test_default_hint_is_applied_when_not_passed() -> None:
    err = ConfigError("bad preset")
    assert err.hint == ConfigError.default_hint
    assert "(hint:" in str(err)


def test_high_traffic_errors_carry_anchored_hints() -> None:
    """At least five errors ship a default hint that links into its own reference section."""
    # Only classes that *declare* their own default_hint (not an inherited one)
    # must anchor to their own section; subclasses may inherit a parent's hint.
    own_hints = {
        name: cls.__dict__["default_hint"]
        for name, cls in _module_exception_classes().items()
        if cls.__dict__.get("default_hint") is not None
    }
    assert len(own_hints) >= 5
    for name, hint in own_hints.items():
        assert "https://dgenio.github.io/contextweaver/errors" in hint
        assert f"#{name.lower()}" in hint, f"{name} hint anchor should target #{name.lower()}"
