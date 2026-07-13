"""Versioned enrichment patch contract for compiled capabilities."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from contextweaver.exceptions import ValidationError

ENRICHMENT_PATCH_VERSION = "contextweaver.compiler.enrichment_patch.v1"
EnrichmentState = Literal["proposed", "accepted", "rejected", "applied"]


@dataclass
class EnrichmentPatch:
    """Auditable metadata patch proposed or accepted during compilation."""

    capability_id: str
    field_path: str
    before: Any = None
    after: Any = None
    state: EnrichmentState = "proposed"
    provenance: dict[str, Any] = field(default_factory=dict)
    version: str = ENRICHMENT_PATCH_VERSION

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "version": self.version,
            "capability_id": self.capability_id,
            "field_path": self.field_path,
            "before": self.before,
            "after": self.after,
            "state": self.state,
            "provenance": dict(self.provenance),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EnrichmentPatch:
        """Deserialise from a JSON-compatible mapping."""
        state = data.get("state", "proposed")
        if state not in ("proposed", "accepted", "rejected", "applied"):
            raise ValidationError(f"invalid enrichment state {state!r}")
        return cls(
            capability_id=str(data["capability_id"]),
            field_path=str(data["field_path"]),
            before=data.get("before"),
            after=data.get("after"),
            state=_enrichment_state(state),
            provenance=dict(data.get("provenance", {})),
            version=str(data.get("version", ENRICHMENT_PATCH_VERSION)),
        )


def _enrichment_state(value: object) -> EnrichmentState:
    if value in ("proposed", "accepted", "rejected", "applied"):
        return value  # type: ignore[return-value]
    raise ValidationError(f"invalid enrichment state {value!r}")
