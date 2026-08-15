"""Configuration dataclasses for the Context Engine and Routing Engine.

All fields have sensible defaults so that callers only need to override what
they care about.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, cast

from contextweaver.exceptions import ConfigError
from contextweaver.types import ItemKind, Phase, Sensitivity

#: Valid values for :attr:`ContextPolicy.sensitivity_action`.  Single source of
#: truth, imported by ``context/sensitivity.py`` so the dataclass validator and
#: the runtime enforcement agree (issue #463).
SENSITIVITY_ACTIONS: tuple[str, ...] = ("drop", "redact")

#: Valid values for :attr:`ContextPolicy.overflow_action` (issue #510).
OVERFLOW_ACTIONS: tuple[str, ...] = ("drop", "warn", "raise")


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


@dataclass
class ScoringConfig:
    """Weights used by the candidate scorer.

    All weights should sum to ≤ 1.0; the remainder is unweighted base score.

    Attributes:
        kind_priority: Optional override for the built-in item-kind priority
            table (issue #487). ``None`` (default) keeps the built-ins in
            :mod:`contextweaver.context.scoring`; supplied values must be in
            ``[0, 1]``. Unlisted kinds fall back to the built-in default.
        phase_overrides: Optional per-:class:`~contextweaver.types.Phase`
            weight overrides (issue #487). A phase present here is scored with
            its own ``ScoringConfig`` (resolution order: phase override →
            this config → built-ins); absent phases use this config unchanged.
            ``dedup_threshold`` is always taken from the base config, never the
            per-phase override. Resolution is one level deep, so a per-phase
            override must not itself define ``phase_overrides``.
    """

    recency_weight: float = 0.3
    tag_match_weight: float = 0.25
    kind_priority_weight: float = 0.35
    token_cost_penalty: float = 0.1
    dedup_threshold: float = 0.85
    kind_priority: dict[ItemKind, float] | None = None
    phase_overrides: dict[Phase, ScoringConfig] | None = None

    def __post_init__(self) -> None:
        """Validate priority values and reject nested phase overrides."""
        for kind, value in (self.kind_priority or {}).items():
            if not 0.0 <= value <= 1.0:
                raise ConfigError(
                    f"ScoringConfig.kind_priority[{kind.value!r}] must be in [0, 1], got {value!r}"
                )
        for phase, cfg in (self.phase_overrides or {}).items():
            if cfg.phase_overrides is not None:
                raise ConfigError(
                    f"ScoringConfig.phase_overrides[{phase.value!r}] must not itself define "
                    "phase_overrides; nested per-phase overrides are not resolved"
                )

    def resolved_for_phase(self, phase: Phase) -> ScoringConfig:
        """Return the effective scoring config for *phase* (issue #487)."""
        if self.phase_overrides is not None and phase in self.phase_overrides:
            return self.phase_overrides[phase]
        return self

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        out: dict[str, Any] = {
            "recency_weight": self.recency_weight,
            "tag_match_weight": self.tag_match_weight,
            "kind_priority_weight": self.kind_priority_weight,
            "token_cost_penalty": self.token_cost_penalty,
            "dedup_threshold": self.dedup_threshold,
        }
        if self.kind_priority is not None:
            out["kind_priority"] = {k.value: v for k, v in self.kind_priority.items()}
        if self.phase_overrides is not None:
            out["phase_overrides"] = {
                p.value: cfg.to_dict() for p, cfg in self.phase_overrides.items()
            }
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScoringConfig:
        """Deserialise from a JSON-compatible dict."""
        _d = cls()
        kind_priority_raw = data.get("kind_priority")
        phase_overrides_raw = data.get("phase_overrides")
        return cls(
            recency_weight=float(data.get("recency_weight", _d.recency_weight)),
            tag_match_weight=float(data.get("tag_match_weight", _d.tag_match_weight)),
            kind_priority_weight=float(data.get("kind_priority_weight", _d.kind_priority_weight)),
            token_cost_penalty=float(data.get("token_cost_penalty", _d.token_cost_penalty)),
            dedup_threshold=float(data.get("dedup_threshold", _d.dedup_threshold)),
            kind_priority={ItemKind(k): float(v) for k, v in kind_priority_raw.items()}
            if kind_priority_raw is not None
            else None,
            phase_overrides={Phase(p): cls.from_dict(cfg) for p, cfg in phase_overrides_raw.items()}
            if phase_overrides_raw is not None
            else None,
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

    def with_phase(self, phase: Phase, tokens: int) -> ContextBudget:
        """Return a copy with *phase*'s budget set to *tokens*."""
        return ContextBudget(
            route=tokens if phase == Phase.route else self.route,
            call=tokens if phase == Phase.call else self.call,
            interpret=tokens if phase == Phase.interpret else self.interpret,
            answer=tokens if phase == Phase.answer else self.answer,
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
        ItemKind.retrieved_doc,
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
            in order. Resolved at runtime by the context manager.
        allow_redacted_drilldown: When ``False`` (default, closed) a
            :meth:`~contextweaver.context.manager.ContextManager.drilldown` whose
            source item meets the sensitivity floor (or was already redacted)
            raises :class:`~contextweaver.exceptions.PolicyViolationError`.
        overflow_action: What to do when budget pressure drops candidates
            (issue #510). ``"drop"`` (default) keeps drop-with-stats behavior;
            ``"warn"`` logs dropped item IDs/reasons; ``"raise"`` raises
            :class:`~contextweaver.exceptions.BudgetOverflowError`.
        overflow_raise_kinds: Optional filter scoping ``"warn"``/``"raise"`` to
            budget drops of these :class:`~contextweaver.types.ItemKind` values.
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
    sensitivity_action: Literal["drop", "redact"] = "drop"
    redaction_hooks: list[str] = field(default_factory=list)
    allow_redacted_drilldown: bool = False
    overflow_action: Literal["drop", "warn", "raise"] = "drop"
    overflow_raise_kinds: list[ItemKind] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate ``sensitivity_action`` / ``overflow_action`` at construction."""
        if self.sensitivity_action not in SENSITIVITY_ACTIONS:
            raise ConfigError(
                f"ContextPolicy.sensitivity_action must be one of {SENSITIVITY_ACTIONS}, "
                f"got {self.sensitivity_action!r}"
            )
        if self.overflow_action not in OVERFLOW_ACTIONS:
            raise ConfigError(
                f"ContextPolicy.overflow_action must be one of {OVERFLOW_ACTIONS}, "
                f"got {self.overflow_action!r}"
            )

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
            "allow_redacted_drilldown": self.allow_redacted_drilldown,
            "overflow_action": self.overflow_action,
            "overflow_raise_kinds": [k.value for k in self.overflow_raise_kinds]
            if self.overflow_raise_kinds is not None
            else None,
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
            sensitivity_action=cast(
                "Literal['drop', 'redact']",
                data.get("sensitivity_action", _d.sensitivity_action),
            ),
            redaction_hooks=list(data.get("redaction_hooks", _d.redaction_hooks)),
            allow_redacted_drilldown=bool(
                data.get("allow_redacted_drilldown", _d.allow_redacted_drilldown)
            ),
            overflow_action=cast(
                "Literal['drop', 'warn', 'raise']",
                data.get("overflow_action", _d.overflow_action),
            ),
            overflow_raise_kinds=[ItemKind(k) for k in data["overflow_raise_kinds"]]
            if data.get("overflow_raise_kinds") is not None
            else None,
            extra=dict(data.get("extra", _d.extra)),
        )


__all__ = [
    "ContextBudget",
    "ContextPolicy",
    "ScoringConfig",
]
