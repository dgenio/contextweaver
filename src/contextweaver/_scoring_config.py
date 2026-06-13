"""``ScoringConfig`` — candidate-scorer weights for the Context Engine.

Extracted from :mod:`contextweaver.config` so that module stays within the
≤300-line convention after gaining per-phase weight overrides and a
configurable kind-priority table (issue #487).  It is re-exported from
:mod:`contextweaver.config` (``from contextweaver.config import ScoringConfig``
keeps working); ``config.py`` remains the public home for the configuration
dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from contextweaver.exceptions import ConfigError
from contextweaver.types import ItemKind, Phase


@dataclass
class ScoringConfig:
    """Weights used by the candidate scorer.

    All weights should sum to ≤ 1.0; the remainder is unweighted base score.

    Attributes:
        kind_priority: Optional override for the built-in item-kind priority
            table (issue #487).  ``None`` (default) keeps the built-ins in
            :mod:`contextweaver.context.scoring`; supplied values must be in
            ``[0, 1]``.  Unlisted kinds fall back to the built-in default.
        phase_overrides: Optional per-:class:`~contextweaver.types.Phase`
            weight overrides (issue #487).  A phase present here is scored with
            its own ``ScoringConfig`` (resolution order: phase override →
            this config → built-ins); absent phases use this config unchanged.
            ``dedup_threshold`` is always taken from the base config, never the
            per-phase override.  Resolution is one level deep, so a per-phase
            override must not itself define ``phase_overrides`` (rejected with
            ``ConfigError``).  ``None`` (default) keeps scoring phase-agnostic
            so default builds are byte-identical to prior releases.
    """

    recency_weight: float = 0.3
    tag_match_weight: float = 0.25
    kind_priority_weight: float = 0.35
    token_cost_penalty: float = 0.1
    dedup_threshold: float = 0.85
    kind_priority: dict[ItemKind, float] | None = None
    phase_overrides: dict[Phase, ScoringConfig] | None = None

    def __post_init__(self) -> None:
        """Validate ``kind_priority`` and reject nested ``phase_overrides`` (#487).

        :meth:`resolved_for_phase` only resolves one level of override, so a
        per-phase config that itself carries ``phase_overrides`` is silently
        ignored — almost always a config mistake.  Rejecting it here turns that
        into an immediate, well-classified error (same fail-early posture as the
        ``kind_priority`` / ``overflow_action`` validation).

        Raises:
            ConfigError: If any ``kind_priority`` value is outside ``[0, 1]``, or
                if a registered phase override itself defines ``phase_overrides``.
        """
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
        """Return the effective scoring config for *phase* (issue #487).

        The per-phase override when one is registered, else this config.
        """
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


__all__ = ["ScoringConfig"]
