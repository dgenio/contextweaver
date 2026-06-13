"""Budget, overflow, and render policy helpers for the context build pipeline.

Extracted from :mod:`contextweaver.context.build` (issue #101 decomposition
discipline) so the build module stays within its size ceiling while gaining the
budget-overflow policy (issue #510) and caller-owned rendering hook (issue
#410).  These functions are pure helpers over already-computed pipeline state;
they hold no manager internals and are not part of the public API.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from contextweaver.config import ContextBudget
from contextweaver.exceptions import BudgetOverflowError

if TYPE_CHECKING:
    from collections.abc import Callable

    from contextweaver.config import ContextPolicy
    from contextweaver.envelope import BuildStats
    from contextweaver.types import ContextItem, Phase

logger = logging.getLogger("contextweaver.context")


def override_phase_budget(
    base: ContextBudget, phase: Phase, budget_tokens: int | None
) -> ContextBudget:
    """Return *base*, or a copy with *phase*'s budget set to *budget_tokens*.

    ``None`` leaves the budget unchanged; only the active phase's limit is
    overridden so the other phases keep their configured values.
    """
    if budget_tokens is None:
        return base
    return ContextBudget(
        route=budget_tokens if phase.value == "route" else base.route,
        call=budget_tokens if phase.value == "call" else base.call,
        interpret=budget_tokens if phase.value == "interpret" else base.interpret,
        answer=budget_tokens if phase.value == "answer" else base.answer,
    )


def adjust_budget_for_header(
    effective_budget: ContextBudget, phase: Phase, hf_tokens: int
) -> ContextBudget:
    """Subtract header/footer token overhead from the active phase's budget."""
    if hf_tokens <= 0:
        return effective_budget
    return ContextBudget(
        route=max(effective_budget.route - hf_tokens, 0)
        if phase.value == "route"
        else effective_budget.route,
        call=max(effective_budget.call - hf_tokens, 0)
        if phase.value == "call"
        else effective_budget.call,
        interpret=max(effective_budget.interpret - hf_tokens, 0)
        if phase.value == "interpret"
        else effective_budget.interpret,
        answer=max(effective_budget.answer - hf_tokens, 0)
        if phase.value == "answer"
        else effective_budget.answer,
    )


def enforce_overflow_policy(
    stats: BuildStats,
    policy: ContextPolicy,
    budget_dropped: list[ContextItem],
) -> None:
    """Honor :attr:`ContextPolicy.overflow_action` for budget drops (issue #510).

    Called after :class:`~contextweaver.envelope.BuildStats` is assembled so the
    would-be stats can be attached to a raised error.  ``"drop"`` (default) is a
    no-op; ``"warn"`` logs the dropped IDs/kinds once; ``"raise"`` raises
    :class:`~contextweaver.exceptions.BudgetOverflowError`.  ``BuildStats``
    accounting is identical in every mode — this only changes the *signal*.

    Args:
        stats: The build's assembled stats (attached to a raised error).
        policy: The active policy carrying ``overflow_action`` /
            ``overflow_raise_kinds``.
        budget_dropped: Items dropped for the ``"budget"`` reason, in pack
            consideration order.

    Raises:
        BudgetOverflowError: When ``overflow_action == "raise"`` and at least
            one in-scope item was dropped for budget.
    """
    if policy.overflow_action == "drop" or not budget_dropped:
        return
    raise_kinds = policy.overflow_raise_kinds
    relevant = [item for item in budget_dropped if raise_kinds is None or item.kind in raise_kinds]
    if not relevant:
        return
    kinds = sorted({item.kind.value for item in relevant})
    ids = sorted(item.id for item in relevant)
    if policy.overflow_action == "warn":
        logger.warning(
            "context build budget overflow: %d item(s) dropped for budget "
            "(reason=budget, ids=%s, kinds=%s)",
            len(relevant),
            ids,
            kinds,
        )
        return
    raise BudgetOverflowError(
        f"context build dropped {len(relevant)} item(s) under budget pressure "
        f"(kinds={kinds}); set ContextPolicy.overflow_action='drop' to allow, "
        f"or raise the phase budget",
        stats=stats,
        dropped_kinds=kinds,
    )


def render_pack_prompt(
    selected: list[ContextItem],
    *,
    full_header: str,
    footer: str,
    renderer: Callable[[list[ContextItem]], str] | None,
) -> str:
    """Render the final prompt string for the selected items (issue #410).

    When *renderer* is supplied the caller owns the entire layout: the renderer
    receives the selected items and its output is used verbatim (the section
    renderer, header, and footer are **not** applied).  When *renderer* is
    ``None`` the default section renderer is used with *full_header* / *footer*,
    preserving the prior behavior exactly.

    Args:
        selected: The budget-selected items, in pack order.
        full_header: The assembled header (empty when a custom renderer is set).
        footer: The caller footer (empty when a custom renderer is set).
        renderer: Optional caller-owned renderer; ``None`` keeps the default.

    Returns:
        The assembled prompt string.
    """
    if renderer is not None:
        return renderer(selected)
    # Local import keeps the data-light helper free of a module-load dependency
    # on the renderer; prompt.py imports only from types.
    from contextweaver.context.prompt import render_context

    return render_context(selected, header=full_header, footer=footer)
