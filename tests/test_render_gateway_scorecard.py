"""Tests for the gateway-scorecard generator's harness contract.

``render_gateway_scorecard.main`` is composed into the unified ``drift_check``
harness (issue #522), which counts non-zero returns. A missing input must
therefore surface as a uniform ``1`` return — not a ``SystemExit`` that would
escape the harness loop and abort the whole ``make drift-check`` run.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import render_gateway_scorecard  # noqa: E402  (import after sys.path manipulation)


def test_missing_input_returns_one_without_raising(tmp_path: Path) -> None:
    """A missing benchmark JSON returns 1 (harness-friendly), never SystemExit."""
    missing = tmp_path / "absent.json"
    rc = render_gateway_scorecard.main(["--check", "--input", str(missing)])
    assert rc == 1


def test_load_returns_none_for_missing_input(tmp_path: Path) -> None:
    assert render_gateway_scorecard._load(tmp_path / "absent.json") is None
