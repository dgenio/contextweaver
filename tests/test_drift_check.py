"""Tests for the unified drift-check harness (issue #522).

The harness composes every registered generator's ``main`` into one gate. These
tests pin the registry shape, the aggregate exit semantics, and that the gate is
green against the committed artifacts.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import drift_check  # noqa: E402  (import after sys.path manipulation)


def test_registry_is_nonempty_and_callable() -> None:
    assert drift_check._GENERATORS
    names = {name for name, _generator in drift_check._GENERATORS}
    assert {"large-catalog", "scenario-routing", "benchmark-trend"} <= names
    for name, generator in drift_check._GENERATORS:
        assert isinstance(name, str) and name
        assert callable(generator)


def test_check_passes_against_committed_artifacts() -> None:
    assert drift_check.main(["--check"]) == 0


def test_aggregates_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    def _ok(argv: Sequence[str] | None = None) -> int:
        return 0

    def _fail(argv: Sequence[str] | None = None) -> int:
        return 1

    monkeypatch.setattr(
        drift_check,
        "_GENERATORS",
        [("ok-one", _ok), ("bad-one", _fail), ("ok-two", _ok)],
    )
    assert drift_check.main(["--check"]) == 1


def test_all_green_returns_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    def _ok(argv: Sequence[str] | None = None) -> int:
        return 0

    monkeypatch.setattr(drift_check, "_GENERATORS", [("a", _ok), ("b", _ok)])
    assert drift_check.main(["--check"]) == 0
