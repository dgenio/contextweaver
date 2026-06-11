"""Tests for the token-calibration benchmark's pure functions (issue #493).

The benchmark is not a CI gate (its tiktoken/provider columns are
environment-dependent), but its corpus shaping and Markdown rendering are
deterministic and worth guarding against regressions. These tests stay
offline-safe: they assert structure and the heuristic relationship, not exact
tiktoken numbers.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "token_calibration",
    Path(__file__).resolve().parents[1] / "benchmarks" / "token_calibration.py",
)
assert _SPEC is not None and _SPEC.loader is not None
token_calibration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(token_calibration)


def test_compute_covers_at_least_four_shapes() -> None:
    snapshot = token_calibration.compute()
    shapes = {row["shape"] for row in snapshot["shapes"]}
    # Acceptance criterion #1: ≥4 corpus shapes.
    assert len(shapes) >= 4
    assert {"prose_en", "prose_cjk", "json", "code", "logs"} <= shapes


def test_compute_heuristic_tokens_are_positive() -> None:
    snapshot = token_calibration.compute()
    for row in snapshot["shapes"]:
        assert row["heuristic_tokens"] > 0
        assert row["chars"] > 0


def test_cjk_density_higher_than_english() -> None:
    """The script-aware heuristic counts CJK far denser than Latin prose (#525)."""
    snapshot = token_calibration.compute()
    rows = {row["shape"]: row for row in snapshot["shapes"]}
    en = rows["prose_en"]
    cjk = rows["prose_cjk"]
    en_density = en["heuristic_tokens"] / en["chars"]
    cjk_density = cjk["heuristic_tokens"] / cjk["chars"]
    assert cjk_density > 3 * en_density


def test_render_markdown_is_a_table() -> None:
    snapshot = token_calibration.compute()
    md = token_calibration.render_markdown(snapshot)
    assert "# Token-estimation calibration" in md
    assert "| Shape |" in md
    assert "`prose_cjk`" in md
    # Deterministic: same snapshot renders byte-identically.
    assert md == token_calibration.render_markdown(snapshot)
