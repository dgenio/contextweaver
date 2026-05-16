"""Tests for the naïve-baseline harness (#215)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import baseline_naive  # noqa: E402

from contextweaver.envelope import BuildStats, ContextPack
from contextweaver.types import ContextItem, ItemKind, Phase


def _pack(prompt: str) -> ContextPack:
    return ContextPack(prompt=prompt, stats=BuildStats(), phase=Phase.answer)


def test_estimate_tokens_char_div_four() -> None:
    """One token per four characters — matches CharDivFourEstimator semantics."""
    assert baseline_naive._estimate_tokens("") == 0
    assert baseline_naive._estimate_tokens("abc") == 0  # < 4 chars
    assert baseline_naive._estimate_tokens("abcd") == 1
    assert baseline_naive._estimate_tokens("a" * 80) == 20


def test_naive_concat_tokens_sums_events() -> None:
    events = [
        ContextItem(id="u1", kind=ItemKind.user_turn, text="a" * 40),
        ContextItem(id="a1", kind=ItemKind.agent_msg, text="b" * 40),
    ]
    # 40 + 1 (\n) + 40 = 81 chars → 20 tokens via char-div-four.
    assert baseline_naive._naive_concat_tokens(events) == 20


def test_pct_reduction_clamped_to_zero() -> None:
    """When the rendered prompt is larger than the naïve concat, reduction = 0%."""
    events = [
        ContextItem(id="u1", kind=ItemKind.user_turn, text="a" * 40),  # naive ≈ 10 tokens
    ]
    pack = _pack("a" * 80)  # rendered ≈ 20 tokens — larger than naive
    delta = baseline_naive.compute_naive_delta(events=events, pack=pack, cw_tokens=20)
    assert delta.pct_reduction == 0.0


def test_pct_reduction_positive() -> None:
    """Stress scenario: naïve concat much larger than cw output → positive reduction."""
    events = [
        ContextItem(id="u1", kind=ItemKind.user_turn, text="a" * 400),  # ≈ 100 tokens
    ]
    pack = _pack("a" * 40)
    delta = baseline_naive.compute_naive_delta(events=events, pack=pack, cw_tokens=10)
    assert delta.naive_tokens == 100
    assert delta.cw_tokens == 10
    assert delta.pct_reduction == pytest.approx(90.0, rel=1e-2)


def test_coverage_pct_full_when_no_parent_chain() -> None:
    """Scenarios without parent_id chains are vacuously fully covered."""
    events = [
        ContextItem(id="u1", kind=ItemKind.user_turn, text="hi"),
        ContextItem(id="a1", kind=ItemKind.agent_msg, text="hello"),
    ]
    pack = _pack("")
    delta = baseline_naive.compute_naive_delta(events=events, pack=pack, cw_tokens=0)
    assert delta.coverage_pct == 100.0


def test_coverage_pct_partial_parent_chain_preserved() -> None:
    """Parent text appearing in the prompt counts as 'preserved'."""
    events = [
        ContextItem(id="u1", kind=ItemKind.user_turn, text="Please look up the deploy status"),
        ContextItem(
            id="tc1",
            kind=ItemKind.tool_call,
            text="deployments.status(env=prod)",
            parent_id="u1",
        ),
        ContextItem(id="tr1", kind=ItemKind.tool_result, text="ok", parent_id="tc1"),
    ]
    # The prompt embeds the parent (u1.text[:40]) but not the tool-call's (tc1.text[:40]).
    pack = _pack("Please look up the deploy status  ...  (tool call omitted)")
    delta = baseline_naive.compute_naive_delta(events=events, pack=pack, cw_tokens=0)
    # 2 children with parent_id (tc1 -> u1 ✓; tr1 -> tc1 ✗) → 50.0%
    assert delta.coverage_pct == 50.0


def test_coverage_pct_unresolvable_parent_counted_as_kept() -> None:
    """Parent id pointing to a non-existent event is vacuously kept (avoids penalising bad data)."""
    events = [
        ContextItem(id="orphan", kind=ItemKind.tool_call, text="x", parent_id="missing"),
    ]
    pack = _pack("")
    delta = baseline_naive.compute_naive_delta(events=events, pack=pack, cw_tokens=0)
    assert delta.coverage_pct == 100.0


def test_compute_naive_delta_deterministic() -> None:
    events = [
        ContextItem(id="u1", kind=ItemKind.user_turn, text="hello"),
        ContextItem(id="a1", kind=ItemKind.agent_msg, text="world"),
    ]
    pack = _pack("hello")
    a = baseline_naive.compute_naive_delta(events=events, pack=pack, cw_tokens=2)
    b = baseline_naive.compute_naive_delta(events=events, pack=pack, cw_tokens=2)
    assert a == b
