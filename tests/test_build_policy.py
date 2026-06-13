"""Tests for contextweaver.context.build_policy (issues #410, #510)."""

from __future__ import annotations

import logging

import pytest

from contextweaver.config import ContextBudget, ContextPolicy
from contextweaver.context.build_policy import (
    adjust_budget_for_header,
    enforce_overflow_policy,
    override_phase_budget,
    render_pack_prompt,
)
from contextweaver.envelope import BuildStats
from contextweaver.exceptions import BudgetOverflowError
from contextweaver.types import ContextItem, ItemKind, Phase


def _item(iid: str, kind: ItemKind = ItemKind.user_turn) -> ContextItem:
    return ContextItem(id=iid, kind=kind, text="x")


# ---------------------------------------------------------------------------
# Budget helpers
# ---------------------------------------------------------------------------


def test_override_phase_budget_none_returns_base() -> None:
    base = ContextBudget()
    assert override_phase_budget(base, Phase.answer, None) is base


def test_override_phase_budget_only_active_phase() -> None:
    base = ContextBudget(route=2000, call=3000, interpret=4000, answer=6000)
    out = override_phase_budget(base, Phase.route, 999)
    assert out.route == 999
    assert out.call == 3000 and out.interpret == 4000 and out.answer == 6000


def test_adjust_budget_for_header_subtracts_only_active_phase() -> None:
    base = ContextBudget(answer=6000)
    out = adjust_budget_for_header(base, Phase.answer, 1000)
    assert out.answer == 5000
    assert out.route == base.route


def test_adjust_budget_for_header_floors_at_zero() -> None:
    out = adjust_budget_for_header(ContextBudget(answer=100), Phase.answer, 500)
    assert out.answer == 0


def test_adjust_budget_for_header_noop_when_no_overhead() -> None:
    base = ContextBudget()
    assert adjust_budget_for_header(base, Phase.answer, 0) is base


# ---------------------------------------------------------------------------
# Overflow policy (#510)
# ---------------------------------------------------------------------------


def test_enforce_overflow_drop_is_noop() -> None:
    # Default "drop" never raises even with budget drops.
    enforce_overflow_policy(BuildStats(), ContextPolicy(), [_item("a")])


def test_enforce_overflow_noop_without_budget_drops() -> None:
    # "raise" with no budget drops is still a no-op.
    enforce_overflow_policy(BuildStats(), ContextPolicy(overflow_action="raise"), [])


def test_enforce_overflow_raise_attaches_stats() -> None:
    stats = BuildStats(dropped_count=3)
    policy = ContextPolicy(overflow_action="raise")
    with pytest.raises(BudgetOverflowError) as excinfo:
        enforce_overflow_policy(stats, policy, [_item("a", ItemKind.policy)])
    err = excinfo.value
    assert err.stats is stats
    assert err.dropped_kinds == ["policy"]


def test_enforce_overflow_raise_scoped_to_kinds() -> None:
    policy = ContextPolicy(overflow_action="raise", overflow_raise_kinds=[ItemKind.policy])
    # A user_turn budget drop is out of scope → no raise.
    enforce_overflow_policy(BuildStats(), policy, [_item("u", ItemKind.user_turn)])
    # A policy budget drop is in scope → raise.
    with pytest.raises(BudgetOverflowError):
        enforce_overflow_policy(BuildStats(), policy, [_item("p", ItemKind.policy)])


def test_enforce_overflow_warn_logs_once(caplog: pytest.LogCaptureFixture) -> None:
    policy = ContextPolicy(overflow_action="warn")
    with caplog.at_level(logging.WARNING, logger="contextweaver.context"):
        enforce_overflow_policy(BuildStats(), policy, [_item("a"), _item("b")])
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "budget overflow" in warnings[0].getMessage()


# ---------------------------------------------------------------------------
# Render hook (#410)
# ---------------------------------------------------------------------------


def test_render_pack_prompt_default_uses_sections() -> None:
    out = render_pack_prompt([_item("u1")], full_header="HDR", footer="FTR", renderer=None)
    assert out.startswith("HDR")
    assert "[USER]" in out
    assert out.endswith("FTR")


def test_render_pack_prompt_custom_renderer_owns_layout() -> None:
    items = [_item("u1"), _item("u2")]
    out = render_pack_prompt(
        items,
        full_header="HDR",
        footer="FTR",
        renderer=lambda sel: "|".join(i.id for i in sel),
    )
    # Custom renderer output is verbatim; header/footer/section are not applied.
    assert out == "u1|u2"
