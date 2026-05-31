"""Tests for the deterministic context-rot demo (#349).

Two paths are covered:

- **Render / gate** — ``render_svg`` is deterministic and the committed
  ``docs/assets/context_rot.svg`` matches the committed
  ``benchmarks/results/context_rot.json``.  ``main(["--check"])`` is the CI
  gate; this pins it at unit level too.
- **Compute** — ``compute_curve`` routes live (exercising the path the
  ``--check`` gate deliberately skips for portability).  Assertions are
  structural so they cannot flake across the CI matrix: the evaluated query
  set stays constant, the model-visible surface stays bounded, and recall
  erodes as distractors pile up.

The demo lives under ``scripts/``, not ``src/``, so it is added to
``sys.path`` the same way :mod:`tests.test_render_scorecard` does.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import context_rot_demo  # noqa: E402  (import after sys.path manipulation)

_REPO_ROOT = Path(__file__).parent.parent
_JSON_PATH = _REPO_ROOT / "benchmarks" / "results" / "context_rot.json"
_SVG_PATH = _REPO_ROOT / "docs" / "assets" / "context_rot.svg"


def test_render_is_deterministic() -> None:
    """Identical payload renders byte-identical SVG on repeated calls."""
    payload = json.loads(_JSON_PATH.read_text(encoding="utf-8"))
    assert context_rot_demo.render_svg(payload) == context_rot_demo.render_svg(payload)


def test_committed_svg_matches_committed_json() -> None:
    """The committed SVG is exactly what the committed JSON renders to."""
    payload = json.loads(_JSON_PATH.read_text(encoding="utf-8"))
    assert context_rot_demo.render_svg(payload) == _SVG_PATH.read_text(encoding="utf-8")


def test_check_cli_passes_on_committed_pair() -> None:
    """The ``--check`` gate (used in CI) passes against the committed artifacts."""
    assert context_rot_demo.main(["--check"]) == 0


def test_committed_json_shape() -> None:
    """Pin the committed curve: bounded shortlist, naive == catalog size."""
    payload = json.loads(_JSON_PATH.read_text(encoding="utf-8"))
    assert payload["top_k"] == 5
    assert payload["gold_cases"] == 200
    points = payload["points"]
    assert [p["catalog_size"] for p in points] == [83, 166, 332, 664, 1328]
    for p in points:
        # Evaluated query set is held constant by keeping every natural tool.
        assert p["queries_evaluated"] == payload["gold_cases"]
        # Naive carries the whole catalog; contextweaver stays bounded by top_k.
        assert p["naive_visible_tools"] == p["catalog_size"]
        assert p["contextweaver_visible_tools"] <= payload["top_k"]
        assert 0.0 <= p["recall_at_5"] <= 1.0
    # Context rot: recall@5 at the largest catalog is well below the smallest.
    assert points[-1]["recall_at_5"] < points[0]["recall_at_5"]


def test_compute_curve_live_is_bounded_and_degrades() -> None:
    """The live routing path keeps the eval set fixed and shows degradation.

    Structural (not exact-value) assertions so the test is portable across
    the CI matrix; exact committed numbers are pinned in
    :func:`test_committed_json_shape`.
    """
    payload = context_rot_demo.compute_curve(sizes=[83, 664])
    assert payload["gold_cases"] == 200
    small, large = payload["points"]
    assert small["queries_evaluated"] == large["queries_evaluated"] == 200
    assert small["contextweaver_visible_tools"] <= 5
    assert large["contextweaver_visible_tools"] <= 5
    assert 0.0 <= large["recall_at_5"] <= small["recall_at_5"] <= 1.0
    # Adding ~580 distractor tools must measurably erode recall.
    assert large["recall_at_5"] < small["recall_at_5"]


def test_compute_curve_is_deterministic() -> None:
    """Routing is deterministic: same sizes -> identical curve numbers."""
    assert context_rot_demo.compute_curve(sizes=[83]) == context_rot_demo.compute_curve(sizes=[83])
