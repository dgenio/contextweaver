"""Tests for the module-size convention gate (issues #456/#753).

The gate freezes pre-existing oversized modules at a grandfathered baseline and
blocks new (or growing) violations. These tests drive the check against
synthetic source trees and derive their sizes from ``LIMIT`` so changing the
reviewed ceiling does not silently leave the test contract on an obsolete
number.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import check_module_size  # noqa: E402  (import after sys.path manipulation)


def _write_module(root: Path, rel: str, lines: int) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(f"x = {i}" for i in range(lines)) + "\n", encoding="utf-8")


def test_passes_on_committed_tree() -> None:
    """The real tree + committed baseline must be green."""
    assert check_module_size.main([]) == 0


def test_new_oversized_module_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "src" / "contextweaver"
    _write_module(src, "ok.py", max(1, check_module_size.LIMIT // 2))
    _write_module(src, "huge.py", check_module_size.LIMIT + 100)
    monkeypatch.setattr(check_module_size, "SRC_ROOT", src)
    monkeypatch.setattr(check_module_size, "BASELINE_PATH", tmp_path / "baseline.json")
    assert check_module_size.check() == 1


def test_grandfathered_module_growth_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "src" / "contextweaver"
    frozen_ceiling = check_module_size.LIMIT + 100
    _write_module(src, "legacy.py", frozen_ceiling + 50)
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"legacy.py": frozen_ceiling}) + "\n", encoding="utf-8")
    monkeypatch.setattr(check_module_size, "SRC_ROOT", src)
    monkeypatch.setattr(check_module_size, "BASELINE_PATH", baseline)
    assert check_module_size.check() == 1


def test_grandfathered_within_ceiling_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src" / "contextweaver"
    frozen_ceiling = check_module_size.LIMIT + 100
    _write_module(src, "legacy.py", check_module_size.LIMIT + 50)
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"legacy.py": frozen_ceiling}) + "\n", encoding="utf-8")
    monkeypatch.setattr(check_module_size, "SRC_ROOT", src)
    monkeypatch.setattr(check_module_size, "BASELINE_PATH", baseline)
    assert check_module_size.check() == 0


def test_exempt_modules_are_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "src" / "contextweaver"
    _write_module(src, "types.py", check_module_size.LIMIT * 3)  # exempt by name
    monkeypatch.setattr(check_module_size, "SRC_ROOT", src)
    monkeypatch.setattr(check_module_size, "BASELINE_PATH", tmp_path / "baseline.json")
    assert check_module_size.check() == 0


def test_update_snapshots_current_oversized_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src" / "contextweaver"
    oversized = check_module_size.LIMIT + 100
    _write_module(src, "small.py", max(1, check_module_size.LIMIT // 2))
    _write_module(src, "big.py", oversized)
    baseline = tmp_path / "baseline.json"
    monkeypatch.setattr(check_module_size, "SRC_ROOT", src)
    monkeypatch.setattr(check_module_size, "BASELINE_PATH", baseline)
    assert check_module_size.main(["--update"]) == 0

    frozen = json.loads(baseline.read_text(encoding="utf-8"))
    assert frozen == {"big.py": oversized}
