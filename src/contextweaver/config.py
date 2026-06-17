"""Configuration dataclasses for the Context Engine and Routing Engine.

All fields have sensible defaults so that callers only need to override what
they care about.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, cast

from contextweaver._scoring_config import ScoringConfig
from contextweaver.exceptions import ConfigError
from contextweaver.types import ItemKind, Phase, Sensitivity

#: Valid values for :attr:`ContextPolicy.sensitivity_action`.  Single source of
#: truth, imported by ``context/sensitivity.py`` so the dataclass validator and
#: the runtime enforcement agree (issue #463).
SENSITIVITY_ACTIONS: tuple[str, ...] = ("drop", "redact")

#: Valid values for :attr:`ContextPolicy.overflow_action` (issue #510).
OVERFLOW_ACTIONS: tuple[str, ...] = ("drop", "warn", "raise")

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
            in order.  Resolved at runtime by the context manager.
        allow_redacted_drilldown: When ``False`` (default, closed) a
            :meth:`~contextweaver.context.manager.ContextManager.drilldown` whose
            source item meets the sensitivity floor (or was already redacted)
            raises :class:`~contextweaver.exceptions.PolicyViolationError`,
            so ``redact``/``drop`` cannot be bypassed by re-fetching the raw
            artifact bytes (issue #451).  Set ``True`` only for deployments that
            intentionally rely on drilldown to recover filtered content.
        overflow_action: What to do when budget pressure drops candidates
            (issue #510).  ``"drop"`` (default) keeps today's silent
            drop-with-stats behavior; ``"warn"`` logs the dropped item IDs and
            reasons once per build; ``"raise"`` raises
            :class:`~contextweaver.exceptions.BudgetOverflowError` with the
            would-be :class:`~contextweaver.envelope.BuildStats` attached.
        overflow_raise_kinds: Optional filter scoping ``"warn"``/``"raise"`` to
            budget drops of these :class:`~contextweaver.types.ItemKind`\\s
            (e.g. ``[ItemKind.policy]``).  ``None`` (default) applies the action
            to any budget drop.
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
        """Validate ``sensitivity_action`` / ``overflow_action`` at construction.

        Validating here (issues #463, #510) turns a config typo into an
        immediate, well-classified error instead of one that surfaces only at
        the first build's sensitivity or selection stage.

        Raises:
            ConfigError: If ``sensitivity_action`` is not one of
                :data:`SENSITIVITY_ACTIONS` or ``overflow_action`` is not one
                of :data:`OVERFLOW_ACTIONS`.
        """
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
            # Cast for the type checker; ``__post_init__`` validates the value
            # at runtime and raises ConfigError on anything outside the literals.
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
