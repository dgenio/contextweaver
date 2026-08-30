"""Regression tests for the naïve baseline measurement method (#841)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import baseline_naive  # noqa: E402


def _scenario(tmp_path: Path) -> Path:
    path = tmp_path / "sample.jsonl"
    path.write_text('{"id":"u1","type":"user_turn","text":"abcdefgh"}\n', encoding="utf-8")
    return path


def test_release_baseline_uses_char_div_four_deterministically() -> None:
    assert baseline_naive.ESTIMATOR_ID == "heuristic/chardiv4"
    assert baseline_naive._count_estimated_tokens("abcdefgh") == 2
    assert baseline_naive._count_estimated_tokens("") == 0


def test_naive_delta_records_measurement_method(tmp_path: Path) -> None:
    delta = baseline_naive.compute_naive_delta(
        _scenario(tmp_path),
        {
            "prompt_tokens": 100,
            "event_count": 10,
            "items_included": 5,
        },
    )
    assert delta["token_estimator"] == "heuristic/chardiv4"
    assert delta["cw_tokens"] == 100
    assert isinstance(delta["naive_tokens"], int)


def test_explicit_mismatched_context_estimator_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="estimator mismatch"):
        baseline_naive.compute_naive_delta(
            _scenario(tmp_path),
            {
                "prompt_tokens": 100,
                "event_count": 10,
                "items_included": 5,
                "token_estimator": "cl100k_base",
            },
        )


def test_tiktoken_presence_cannot_change_release_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = {"prompt_tokens": 100, "event_count": 10, "items_included": 5}
    first = baseline_naive.compute_naive_delta(_scenario(tmp_path), row)
    monkeypatch.setitem(sys.modules, "tiktoken", object())
    second = baseline_naive.compute_naive_delta(_scenario(tmp_path), row)
    assert second == first
