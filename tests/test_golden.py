"""Tests for the shared golden-file drift primitives (issue #522).

These back every ``make <x>-check`` gate and the unified ``make drift-check``
harness, so the compare/write/report contract is pinned here at unit level.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import _golden  # noqa: E402  (import after sys.path manipulation)


def test_drifted_paths_flags_missing_and_changed(tmp_path: Path) -> None:
    clean = tmp_path / "clean.txt"
    clean.write_text("expected\n", encoding="utf-8")
    changed = tmp_path / "changed.txt"
    changed.write_text("stale\n", encoding="utf-8")
    missing = tmp_path / "missing.txt"

    rendered = {clean: "expected\n", changed: "fresh\n", missing: "new\n"}
    drifted = _golden.drifted_paths(rendered)

    assert clean not in drifted
    assert set(drifted) == {changed, missing}


def test_check_text_artifacts_clean(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "a.txt"
    target.write_text("body\n", encoding="utf-8")
    rc = _golden.check_text_artifacts({target: "body\n"}, label="thing", regen="make thing")
    assert rc == 0
    assert "up to date" in capsys.readouterr().out


def test_check_text_artifacts_reports_drift(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "a.txt"
    target.write_text("old\n", encoding="utf-8")
    rc = _golden.check_text_artifacts({target: "new\n"}, label="thing", regen="make thing")
    assert rc == 1
    err = capsys.readouterr().err
    assert "make thing" in err and "thing" in err


def test_write_text_artifacts_creates_dirs_and_uses_lf(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "out.txt"
    _golden.write_text_artifacts({target: "line1\nline2\n"})
    # Bytes, not text — prove no CRLF translation crept in.
    assert target.read_bytes() == b"line1\nline2\n"
