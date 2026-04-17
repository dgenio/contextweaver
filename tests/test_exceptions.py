"""Tests for contextweaver.exceptions."""

from __future__ import annotations

import pytest

from contextweaver.exceptions import (
    ArtifactNotFoundError,
    BudgetExceededError,
    CatalogError,
    ContextWeaverError,
    DuplicateItemError,
    GraphBuildError,
    ItemNotFoundError,
    PolicyViolationError,
    RouteError,
)


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
    assert str(err) == "test message"


def test_base_exception_catchall() -> None:
    with pytest.raises(ContextWeaverError):
        raise ItemNotFoundError("missing")


def test_specific_catch() -> None:
    with pytest.raises(ArtifactNotFoundError):
        raise ArtifactNotFoundError("handle-xyz")
