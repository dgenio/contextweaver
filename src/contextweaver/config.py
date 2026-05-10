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
    dedup_threshold: float = 0.85

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "recency_weight": self.recency_weight,
            "tag_match_weight": self.tag_match_weight,
            "kind_priority_weight": self.kind_priority_weight,
            "token_cost_penalty": self.token_cost_penalty,
            "dedup_threshold": self.dedup_threshold,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScoringConfig:
        """Deserialise from a JSON-compatible dict."""
        _d = cls()
        return cls(
            recency_weight=float(data.get("recency_weight", _d.recency_weight)),
            tag_match_weight=float(data.get("tag_match_weight", _d.tag_match_weight)),
            kind_priority_weight=float(data.get("kind_priority_weight", _d.kind_priority_weight)),
            token_cost_penalty=float(data.get("token_cost_penalty", _d.token_cost_penalty)),
            dedup_threshold=float(data.get("dedup_threshold", _d.dedup_threshold)),
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
        _d = cls()
        return cls(
            route=int(data.get("route", _d.route)),
            call=int(data.get("call", _d.call)),
            interpret=int(data.get("interpret", _d.interpret)),
            answer=int(data.get("answer", _d.answer)),
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
        sensitivity_floor: Items at or above this sensitivity level are
            dropped or redacted (depending on ``sensitivity_action``).
        sensitivity_action: ``"drop"`` (default) removes items at or above
            the floor; ``"redact"`` replaces their text via redaction hooks.
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
    sensitivity_floor: Sensitivity = Sensitivity.confidential
    sensitivity_action: str = "drop"
    redaction_hooks: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "allowed_kinds_per_phase": {
                phase.value: [k.value for k in kinds]
                for phase, kinds in self.allowed_kinds_per_phase.items()
            },
            "max_items_per_kind": {k.value: v for k, v in self.max_items_per_kind.items()},
            "sensitivity_floor": self.sensitivity_floor.value,
            "sensitivity_action": self.sensitivity_action,
            "redaction_hooks": list(self.redaction_hooks),
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextPolicy:
        """Deserialise from a JSON-compatible dict."""
        allowed_raw = data.get("allowed_kinds_per_phase")
        allowed: dict[Phase, list[ItemKind]] | None = None
        if allowed_raw is not None:
            allowed = {
                Phase(phase_str): [ItemKind(k) for k in kinds]
                for phase_str, kinds in allowed_raw.items()
            }

        max_raw = data.get("max_items_per_kind")
        max_items: dict[ItemKind, int] | None = None
        if max_raw is not None:
            max_items = {ItemKind(k): int(v) for k, v in max_raw.items()}

        _d = cls()
        return cls(
            allowed_kinds_per_phase=allowed if allowed is not None else _d.allowed_kinds_per_phase,
            max_items_per_kind=max_items if max_items is not None else _d.max_items_per_kind,
            sensitivity_floor=Sensitivity(data["sensitivity_floor"])
            if "sensitivity_floor" in data
            else _d.sensitivity_floor,
            sensitivity_action=str(data.get("sensitivity_action", _d.sensitivity_action)),
            redaction_hooks=list(data.get("redaction_hooks", _d.redaction_hooks)),
            extra=dict(data.get("extra", _d.extra)),
        )


__all__ = [
    "ContextBudget",
    "ContextPolicy",
    "ScoringConfig",
]
