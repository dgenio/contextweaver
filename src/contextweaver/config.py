"""Configuration dataclasses for the Context Engine and Routing Engine.

All fields have sensible defaults so that callers only need to override what
they care about.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from contextweaver.types import ItemKind, Phase, Sensitivity

# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


@dataclass
class ScoringConfig:
    """Weights used by the candidate scorer.

    All weights should sum to ≤ 1.0; the remainder is unweighted base score.
    """

    recency_weight: float = 0.3
    tag_match_weight: float = 0.25
    kind_priority_weight: float = 0.35
    token_cost_penalty: float = 0.1

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "recency_weight": self.recency_weight,
            "tag_match_weight": self.tag_match_weight,
            "kind_priority_weight": self.kind_priority_weight,
            "token_cost_penalty": self.token_cost_penalty,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScoringConfig:
        """Deserialise from a JSON-compatible dict."""
        return cls(
            recency_weight=float(data.get("recency_weight", 0.3)),
            tag_match_weight=float(data.get("tag_match_weight", 0.25)),
            kind_priority_weight=float(data.get("kind_priority_weight", 0.35)),
            token_cost_penalty=float(data.get("token_cost_penalty", 0.1)),
        )


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


@dataclass
class ContextBudget:
    """Per-phase token budgets for context compilation.

    Defaults are intentionally conservative and should be tuned per model.
    """

    route: int = 2000
    call: int = 3000
    interpret: int = 4000
    answer: int = 6000

    def for_phase(self, phase: Phase) -> int:
        """Return the token budget for *phase*.

        Args:
            phase: The active execution phase.

        Returns:
            The maximum number of tokens allowed in the compiled context.
        """
        return int(getattr(self, phase.value))

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "route": self.route,
            "call": self.call,
            "interpret": self.interpret,
            "answer": self.answer,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextBudget:
        """Deserialise from a JSON-compatible dict."""
        return cls(
            route=int(data.get("route", 2000)),
            call=int(data.get("call", 3000)),
            interpret=int(data.get("interpret", 4000)),
            answer=int(data.get("answer", 6000)),
        )


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

_DEFAULT_ALLOWED_KINDS: dict[Phase, list[ItemKind]] = {
    Phase.route: [
        ItemKind.user_turn,
        ItemKind.plan_state,
        ItemKind.policy,
    ],
    Phase.call: [
        ItemKind.user_turn,
        ItemKind.agent_msg,
        ItemKind.tool_call,
        ItemKind.plan_state,
        ItemKind.policy,
    ],
    Phase.interpret: [
        ItemKind.user_turn,
        ItemKind.agent_msg,
        ItemKind.tool_call,
        ItemKind.tool_result,
        ItemKind.doc_snippet,
        ItemKind.memory_fact,
        ItemKind.plan_state,
        ItemKind.policy,
    ],
    Phase.answer: list(ItemKind),
}


@dataclass
class ContextPolicy:
    """Policy constraints applied during context compilation.

    Attributes:
        allowed_kinds_per_phase: Mapping from phase to the set of item kinds
            permitted in that phase.
        max_items_per_kind: Maximum number of items per :class:`~contextweaver.types.ItemKind`
            included in a single context build.
        ttl_behavior: How to handle items that have exceeded their TTL.
            ``"drop"`` removes them; ``"warn"`` keeps them but fires a hook.
        sensitivity_floor: Items at or above this sensitivity level are
            subject to redaction hooks before being included.
        redaction_hooks: Names of redaction hook implementations to apply,
            in order.  Resolved at runtime by the context manager.
    """

    allowed_kinds_per_phase: dict[Phase, list[ItemKind]] = field(
        default_factory=lambda: {
            phase: list(kinds) for phase, kinds in _DEFAULT_ALLOWED_KINDS.items()
        }
    )
    max_items_per_kind: dict[ItemKind, int] = field(
        default_factory=lambda: {k: 50 for k in ItemKind}
    )
    ttl_behavior: str = "drop"
    sensitivity_floor: Sensitivity = Sensitivity.confidential
    redaction_hooks: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        akpp = self.allowed_kinds_per_phase
        return {
            "allowed_kinds_per_phase": {
                phase.value: [k.value for k in kinds]
                for phase, kinds in sorted(
                    akpp.items(), key=lambda p: p[0].value
                )
            },
            "max_items_per_kind": {
                k.value: v
                for k, v in sorted(
                    self.max_items_per_kind.items(),
                    key=lambda p: p[0].value,
                )
            },
            "ttl_behavior": self.ttl_behavior,
            "sensitivity_floor": self.sensitivity_floor.value,
            "redaction_hooks": list(self.redaction_hooks),
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextPolicy:
        """Deserialise from a JSON-compatible dict."""
        raw_allowed = data.get("allowed_kinds_per_phase", {})
        allowed: dict[Phase, list[ItemKind]] = {
            Phase(p): [ItemKind(k) for k in kinds]
            for p, kinds in raw_allowed.items()
        }
        raw_max = data.get("max_items_per_kind", {})
        max_items: dict[ItemKind, int] = {
            ItemKind(k): int(v) for k, v in raw_max.items()
        }
        return cls(
            allowed_kinds_per_phase=allowed if allowed else _DEFAULT_ALLOWED_KINDS.copy(),
            max_items_per_kind=max_items if max_items else {k: 50 for k in ItemKind},
            ttl_behavior=str(data.get("ttl_behavior", "drop")),
            sensitivity_floor=Sensitivity(data.get("sensitivity_floor", "confidential")),
            redaction_hooks=list(data.get("redaction_hooks", [])),
            extra=dict(data.get("extra", {})),
        )
