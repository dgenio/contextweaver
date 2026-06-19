"""Tests for contextweaver._verify (issue #657).

Unit tests covering both happy path and deterministic failure modes for
each verification check.  Failure tests ensure ``ok=False``, ``detail``
carries the exception, and ``fix_hint`` is present so the CLI can surface
actionable guidance.
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from contextweaver._verify import (
    _check_build,
    _check_import,
    _check_manager,
    _check_routing,
    _check_tokens,
)

# ------------------------------------------------------------------
# Happy path
# ------------------------------------------------------------------


def test_check_import_passes() -> None:
    """Import check returns ok=True with a version string."""
    check = _check_import()
    assert check.ok
    assert "version" in check.detail.lower()


def test_check_manager_passes() -> None:
    """Manager check returns ok=True with store counts."""
    check = _check_manager()
    assert check.ok
    assert "event_log=" in check.detail


def test_check_build_passes() -> None:
    """Build check returns ok=True with token and item counts."""
    check = _check_build()
    assert check.ok
    assert "prompt_tokens=" in check.detail


def test_check_tokens_passes() -> None:
    """Token check returns ok=True with a positive count."""
    check = _check_tokens()
    assert check.ok
    assert "count=" in check.detail


def test_check_routing_passes() -> None:
    """Routing check returns ok=True with candidate count and top-1 id."""
    check = _check_routing()
    assert check.ok
    assert "candidates=" in check.detail


# ------------------------------------------------------------------
# Failure modes
# ------------------------------------------------------------------


def test_check_import_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Import check catches missing module and surfaces a fix hint."""
    saved = sys.modules.get("contextweaver._version")
    monkeypatch.setitem(sys.modules, "contextweaver._version", None)
    try:
        check = _check_import()
        assert not check.ok
        assert check.fix_hint is not None
        assert "pip install" in check.fix_hint.lower()
    finally:
        if saved is not None:
            monkeypatch.setitem(sys.modules, "contextweaver._version", saved)
        else:
            monkeypatch.delitem(sys.modules, "contextweaver._version", raising=False)


def test_check_manager_failure() -> None:
    """Manager check catches ContextManager init errors."""
    with patch("contextweaver.context.manager.ContextManager") as mock:
        mock.side_effect = RuntimeError("simulated manager failure")
        check = _check_manager()
        assert not check.ok
        assert "simulated manager failure" in check.detail
        assert check.fix_hint is not None


def test_check_build_failure() -> None:
    """Build check catches errors during ContextManager use."""
    with patch("contextweaver.context.manager.ContextManager") as mock_cls:
        mock_cls.side_effect = RuntimeError("simulated build failure")
        check = _check_build()
        assert not check.ok
        assert "simulated build failure" in check.detail
        assert check.fix_hint is not None
        assert "github.com/dgenio/contextweaver/issues" in check.fix_hint


def test_check_tokens_failure() -> None:
    """Token check catches counter errors."""
    with patch("contextweaver.tokens.heuristic_counter") as mock:
        mock.side_effect = RuntimeError("simulated token failure")
        check = _check_tokens()
        assert not check.ok
        assert "simulated token failure" in check.detail
        assert check.fix_hint is not None


def test_check_routing_failure() -> None:
    """Routing check catches TreeBuilder or Router errors."""
    with patch("contextweaver.routing.tree.TreeBuilder") as mock:
        mock.side_effect = RuntimeError("simulated routing failure")
        check = _check_routing()
        assert not check.ok
        assert "simulated routing failure" in check.detail
        assert check.fix_hint is not None


# ------------------------------------------------------------------
# Pinned network-free estimator (issue #705)
# ------------------------------------------------------------------


def test_check_manager_and_build_pin_heuristic_estimator() -> None:
    """``verify`` must not rely on ContextManager's default estimator (#705).

    Both checks pass an explicit ``heuristic_counter()``; the default
    ``HeuristicEstimator`` constructor must therefore never run during verify.
    Patching it to raise proves the pin holds: without the explicit estimator
    these checks would fall through to the default and report ``ok=False``.
    """
    sentinel = "default estimator must not be constructed during verify"
    with patch(
        "contextweaver.context.manager.HeuristicEstimator",
        side_effect=AssertionError(sentinel),
    ):
        manager_check = _check_manager()
        build_check = _check_build()

    assert manager_check.ok, manager_check.detail
    assert build_check.ok, build_check.detail
