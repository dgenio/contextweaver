"""Configuration dataclasses for the Context Engine and Routing Engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from contextweaver.protocols import RedactionHook
from contextweaver.types import ItemKind, Phase, Sensitivity

# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


@dataclass
class ScoringConfig:
    """Weights used by the candidate scorer."""

    recency_weight: float = 0.3
    tag_match_weight: float = 0.25
    kind_priority_weight: float = 0.35
    token_cost_penalty: float = 0.1


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


@dataclass
class ContextBudget:
    """Per-phase token budgets for context compilation."""

    route: int = 2000
    call: int = 3000
    interpret: int = 4000
    answer: int = 6000

    def for_phase(self, phase: Phase) -> int:
        """Return the token budget for *phase*."""
        return int(getattr(self, phase.value))


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

_DEFAULT_ALLOWED_KINDS: dict[Phase, list[ItemKind]] = {
    Phase.ROUTE: [
        ItemKind.USER_TURN,
        ItemKind.PLAN_STATE,
        ItemKind.POLICY,
    ],
    Phase.CALL: [
        ItemKind.USER_TURN,
        ItemKind.AGENT_MSG,
        ItemKind.TOOL_CALL,
        ItemKind.PLAN_STATE,
        ItemKind.POLICY,
    ],
    Phase.INTERPRET: [
        ItemKind.USER_TURN,
        ItemKind.AGENT_MSG,
        ItemKind.TOOL_CALL,
        ItemKind.TOOL_RESULT,
        ItemKind.DOC_SNIPPET,
        ItemKind.MEMORY_FACT,
        ItemKind.PLAN_STATE,
        ItemKind.POLICY,
    ],
    Phase.ANSWER: list(ItemKind),
}


@dataclass
class ContextPolicy:
    """Policy constraints applied during context compilation."""

    allowed_kinds_per_phase: dict[Phase, list[ItemKind]] = field(
        default_factory=lambda: {
            phase: list(kinds) for phase, kinds in _DEFAULT_ALLOWED_KINDS.items()
        }
    )
    max_items_per_kind: dict[ItemKind, int] = field(
        default_factory=lambda: {k: 50 for k in ItemKind}
    )
    ttl_behavior: Literal["hard_drop", "deprioritize"] = "hard_drop"
    sensitivity_floor: Sensitivity = Sensitivity.CONFIDENTIAL
    redaction_hooks: list[RedactionHook] = field(default_factory=list)
