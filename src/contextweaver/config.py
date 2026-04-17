"""Configuration dataclasses for the Context Engine and Routing Engine.

All fields have sensible defaults so that callers only need to override what
they care about.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from contextweaver.exceptions import ConfigError
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


# ---------------------------------------------------------------------------
# Routing configuration
# ---------------------------------------------------------------------------

# Named-preset definitions: (beam_width, max_depth, top_k, confidence_gap, max_children, answer)
_ROUTING_PRESETS: dict[str, tuple[int, int, int, float, int, int]] = {
    "fast": (1, 4, 5, 0.20, 15, 3000),
    "balanced": (2, 8, 10, 0.15, 20, 6000),
    "accurate": (4, 12, 20, 0.10, 30, 8000),
}


@dataclass
class RoutingConfig:
    """Parameters that control the beam-search router.

    Attributes:
        beam_width: Number of beams to keep at each tree level.
        max_depth: Maximum tree depth to traverse.
        top_k: Maximum number of results to return.
        confidence_gap: Minimum score gap between rank-1 and rank-2 to
            consider the top pick confident.  Must be in ``[0.0, 1.0]``.
        max_children: Maximum number of children per graph node.
    """

    beam_width: int = 2
    max_depth: int = 8
    top_k: int = 10
    confidence_gap: float = 0.15
    max_children: int = 20

    def routing_kwargs(self) -> dict[str, Any]:
        """Return router constructor kwargs (excludes *max_children*).

        Returns:
            A dict suitable for ``**``-unpacking into :class:`~contextweaver.routing.router.Router`.
        """
        return {
            "beam_width": self.beam_width,
            "max_depth": self.max_depth,
            "top_k": self.top_k,
            "confidence_gap": self.confidence_gap,
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "beam_width": self.beam_width,
            "max_depth": self.max_depth,
            "top_k": self.top_k,
            "confidence_gap": self.confidence_gap,
            "max_children": self.max_children,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RoutingConfig:
        """Deserialise from a JSON-compatible dict."""
        _d = cls()
        return cls(
            beam_width=int(data.get("beam_width", _d.beam_width)),
            max_depth=int(data.get("max_depth", _d.max_depth)),
            top_k=int(data.get("top_k", _d.top_k)),
            confidence_gap=float(data.get("confidence_gap", _d.confidence_gap)),
            max_children=int(data.get("max_children", _d.max_children)),
        )


# ---------------------------------------------------------------------------
# Profile — bundles all config objects + named presets
# ---------------------------------------------------------------------------


@dataclass
class ProfileConfig:
    """Unified configuration profile bundling all contextweaver config objects.

    Use :meth:`from_preset` to get a named starting-point configuration, then
    override individual fields as needed.

    Example::

        profile = ProfileConfig.from_preset("fast")
        router = Router(graph, items=catalog.all(), **profile.routing.routing_kwargs())

    Attributes:
        budget: Per-phase token budgets for the context engine.
        policy: Policy constraints for the context engine.
        scoring: Scoring weights for candidate ranking.
        routing: Beam-search parameters for the routing engine.
    """

    budget: ContextBudget = field(default_factory=ContextBudget)
    policy: ContextPolicy = field(default_factory=ContextPolicy)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    routing: RoutingConfig = field(default_factory=RoutingConfig)

    @classmethod
    def from_preset(cls, name: str) -> ProfileConfig:
        """Construct a :class:`ProfileConfig` from a named preset.

        Supported presets:

        * ``"fast"`` — minimal search breadth; lowest latency and token cost.
        * ``"balanced"`` — matches the :class:`~contextweaver.routing.router.Router`
          constructor defaults; good general-purpose starting point.
        * ``"accurate"`` — wide beam search; highest recall at higher cost.

        Args:
            name: One of ``"fast"``, ``"balanced"``, or ``"accurate"``.

        Returns:
            A fully populated :class:`ProfileConfig`.

        Raises:
            ConfigError: If *name* is not a recognised preset.
        """
        if name not in _ROUTING_PRESETS:
            valid = ", ".join(f'"{k}"' for k in sorted(_ROUTING_PRESETS))
            raise ConfigError(f"Unknown preset {name!r}. Valid presets: {valid}.")

        beam_width, max_depth, top_k, confidence_gap, max_children, answer = _ROUTING_PRESETS[name]

        return cls(
            budget=ContextBudget(answer=answer),
            routing=RoutingConfig(
                beam_width=beam_width,
                max_depth=max_depth,
                top_k=top_k,
                confidence_gap=confidence_gap,
                max_children=max_children,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict.

        Note:
            The ``policy`` field is intentionally excluded because
            :class:`ContextPolicy` contains enum-keyed dicts that are not
            trivially JSON-serialisable.  Callers round-tripping via
            :meth:`from_dict` will receive a fresh :class:`ContextPolicy`
            with default values.
        """
        return {
            "budget": {
                "route": self.budget.route,
                "call": self.budget.call,
                "interpret": self.budget.interpret,
                "answer": self.budget.answer,
            },
            "scoring": {
                "recency_weight": self.scoring.recency_weight,
                "tag_match_weight": self.scoring.tag_match_weight,
                "kind_priority_weight": self.scoring.kind_priority_weight,
                "token_cost_penalty": self.scoring.token_cost_penalty,
            },
            "routing": self.routing.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProfileConfig:
        """Deserialise from a JSON-compatible dict.

        Note:
            The ``policy`` field is not serialised by :meth:`to_dict` and will
            be reset to :class:`ContextPolicy` defaults on round-trip.
        """
        b = data.get("budget", {})
        s = data.get("scoring", {})
        _b = ContextBudget()
        _s = ScoringConfig()
        budget = ContextBudget(
            route=int(b.get("route", _b.route)),
            call=int(b.get("call", _b.call)),
            interpret=int(b.get("interpret", _b.interpret)),
            answer=int(b.get("answer", _b.answer)),
        )
        scoring = ScoringConfig(
            recency_weight=float(s.get("recency_weight", _s.recency_weight)),
            tag_match_weight=float(s.get("tag_match_weight", _s.tag_match_weight)),
            kind_priority_weight=float(s.get("kind_priority_weight", _s.kind_priority_weight)),
            token_cost_penalty=float(s.get("token_cost_penalty", _s.token_cost_penalty)),
        )
        routing = RoutingConfig.from_dict(data.get("routing", {}))
        return cls(budget=budget, scoring=scoring, routing=routing)
