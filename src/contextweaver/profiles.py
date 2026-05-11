"""Routing and profile configuration for contextweaver.

Contains :class:`Mode` (determinism mode), :class:`RoutingConfig`
(beam-search parameters), and :class:`ProfileConfig` (unified configuration
bundle with named presets).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from contextweaver.config import ContextBudget, ContextPolicy, ScoringConfig
from contextweaver.exceptions import ConfigError

# ---------------------------------------------------------------------------
# Execution mode
# ---------------------------------------------------------------------------


class Mode(str, Enum):
    """Determinism mode for routing and context pipelines.

    The mode controls how much non-determinism the engine is allowed to
    introduce.  All current pipeline stages are deterministic; the modes
    exist as a forward-compatible knob for future LLM-backed stages.

    Values:
        strict: Full determinism — no LLM calls, no randomness, identical
            inputs always produce identical outputs.  This is the default
            and matches all current behaviour.
        seeded: Deterministic with an explicit seed parameter — allows
            randomness-using algorithms (e.g. embedding-based ANN) as long
            as the seed is fixed.  Reserved for forward compatibility.
        adaptive: FUTURE.  Engine may learn from telemetry / prior runs.
            Not currently honoured by any pipeline stage; selecting this
            mode is accepted but has no effect today.
    """

    strict = "strict"
    seeded = "seeded"
    adaptive = "adaptive"


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


@dataclass
class ProfileConfig:
    """Unified configuration profile bundling all contextweaver config objects.

    Use :meth:`from_preset` to get a named starting-point configuration, then
    override individual fields as needed.

    Example::

        profile = ProfileConfig.from_preset("fast")
        router = Router(graph, items=catalog.all(), **profile.routing.routing_kwargs())

    Attributes:
        mode: Determinism mode (default :attr:`Mode.strict`).  ``seeded`` and
            ``adaptive`` are reserved for future LLM-backed pipeline stages
            and currently have no effect on routing or context output.
        budget: Per-phase token budgets for the context engine.
        policy: Policy constraints for the context engine.
        scoring: Scoring weights for candidate ranking.
        routing: Beam-search parameters for the routing engine.
        seed: Optional integer seed used when *mode* is :attr:`Mode.seeded`.
            Reserved for forward compatibility; ignored in ``strict`` mode.
    """

    mode: Mode = Mode.strict
    budget: ContextBudget = field(default_factory=ContextBudget)
    policy: ContextPolicy = field(default_factory=ContextPolicy)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    routing: RoutingConfig = field(default_factory=RoutingConfig)
    seed: int | None = None

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

    @classmethod
    def from_profile(cls, name: str) -> ProfileConfig:
        """Alias for :meth:`from_preset` matching the issue-tracker naming."""
        return cls.from_preset(name)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict.

        All four sub-configs plus :attr:`mode` and :attr:`seed` are included:
        ``mode``, ``seed``, ``budget``, ``policy``, ``scoring``, and
        ``routing``. ``policy`` is serialised in full via
        :meth:`ContextPolicy.to_dict`; the round-trip through
        :meth:`from_dict` is lossless for default and custom values alike.
        """
        return {
            "mode": self.mode.value,
            "seed": self.seed,
            "budget": self.budget.to_dict(),
            "policy": self.policy.to_dict(),
            "scoring": self.scoring.to_dict(),
            "routing": self.routing.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProfileConfig:
        """Deserialise from a JSON-compatible dict.

        Raises:
            ConfigError: When ``mode`` is set to an unknown value.
        """
        mode_raw = data.get("mode", Mode.strict.value)
        try:
            mode = Mode(mode_raw)
        except ValueError as exc:
            valid = ", ".join(f'"{m.value}"' for m in Mode)
            raise ConfigError(f"Unknown mode {mode_raw!r}. Valid modes: {valid}.") from exc
        seed_raw = data.get("seed")
        seed = int(seed_raw) if seed_raw is not None else None
        budget = ContextBudget.from_dict(data.get("budget", {}))
        policy = ContextPolicy.from_dict(data.get("policy", {}))
        scoring = ScoringConfig.from_dict(data.get("scoring", {}))
        routing = RoutingConfig.from_dict(data.get("routing", {}))
        return cls(
            mode=mode, budget=budget, policy=policy, scoring=scoring, routing=routing, seed=seed
        )
