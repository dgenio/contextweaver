"""Source snapshot contracts for the offline capability compiler."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from contextweaver.compiler._json import digest_json
from contextweaver.compiler.resources import ResourceDescriptor
from contextweaver.exceptions import ValidationError
from contextweaver.types import SelectableItem

SourceRequirement = Literal["required", "optional", "fallback"]
SourceState = Literal["available", "degraded", "missing", "unsupported"]


@dataclass
class SourceCoverage:
    """Coverage and failure semantics for one source snapshot."""

    source_id: str
    requirement: SourceRequirement = "required"
    state: SourceState = "available"
    capability_ids: list[str] = field(default_factory=list)
    missing_capability_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    semantic_loss: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "source_id": self.source_id,
            "requirement": self.requirement,
            "state": self.state,
            "capability_ids": list(self.capability_ids),
            "missing_capability_ids": list(self.missing_capability_ids),
            "warnings": list(self.warnings),
            "semantic_loss": list(self.semantic_loss),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SourceCoverage:
        """Deserialise from a JSON-compatible mapping."""
        return cls(
            source_id=str(data["source_id"]),
            requirement=_source_requirement(data.get("requirement", "required")),
            state=_source_state(data.get("state", "available")),
            capability_ids=[str(v) for v in data.get("capability_ids", [])],
            missing_capability_ids=[str(v) for v in data.get("missing_capability_ids", [])],
            warnings=[str(v) for v in data.get("warnings", [])],
            semantic_loss=[str(v) for v in data.get("semantic_loss", [])],
        )


@dataclass
class CapabilitySourceSnapshot:
    """Deterministic snapshot emitted by a capability source adapter."""

    source_id: str
    source_type: str
    source_version: str
    adapter_id: str
    adapter_version: str
    capabilities: list[SelectableItem] = field(default_factory=list)
    resources: list[ResourceDescriptor] = field(default_factory=list)
    runtime_bindings: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    captured_at: str = ""
    coverage: SourceCoverage | None = None
    warnings: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    semantic_loss: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict, including the source digest."""
        payload = self._payload()
        payload["digest"] = self.digest()
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CapabilitySourceSnapshot:
        """Deserialise from a JSON-compatible mapping."""
        coverage = data.get("coverage")
        return cls(
            source_id=str(data["source_id"]),
            source_type=str(data["source_type"]),
            source_version=str(data.get("source_version", "")),
            adapter_id=str(data["adapter_id"]),
            adapter_version=str(data.get("adapter_version", "")),
            capabilities=[
                SelectableItem.from_dict(dict(raw)) for raw in data.get("capabilities", [])
            ],
            resources=[
                ResourceDescriptor.from_dict(dict(raw)) for raw in data.get("resources", [])
            ],
            runtime_bindings=dict(data.get("runtime_bindings", {})),
            metadata=dict(data.get("metadata", {})),
            captured_at=str(data.get("captured_at", "")),
            coverage=(
                SourceCoverage.from_dict(dict(coverage)) if isinstance(coverage, Mapping) else None
            ),
            warnings=[str(v) for v in data.get("warnings", [])],
            unsupported=[str(v) for v in data.get("unsupported", [])],
            semantic_loss=[str(v) for v in data.get("semantic_loss", [])],
        )

    def digest(self) -> str:
        """Return the deterministic source digest."""
        return digest_json(self._payload())

    def _payload(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "source_version": self.source_version,
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "capabilities": [
                item.to_dict() for item in sorted(self.capabilities, key=lambda it: it.id)
            ],
            "resources": [
                res.to_dict() for res in sorted(self.resources, key=lambda res: res.resource_id)
            ],
            "runtime_bindings": dict(self.runtime_bindings),
            "metadata": dict(self.metadata),
            "captured_at": self.captured_at,
            "coverage": self.coverage.to_dict() if self.coverage else None,
            "warnings": list(self.warnings),
            "unsupported": list(self.unsupported),
            "semantic_loss": list(self.semantic_loss),
        }


class CapabilitySourceAdapter(Protocol):
    """Protocol implemented by source-specific compiler adapters."""

    adapter_id: str
    adapter_version: str

    def discover(self) -> CapabilitySourceSnapshot:
        """Return a deterministic source snapshot without executing capabilities."""


def _source_requirement(value: object) -> SourceRequirement:
    if value in ("required", "optional", "fallback"):
        return value
    raise ValidationError(f"invalid source requirement {value!r}")


def _source_state(value: object) -> SourceState:
    if value in ("available", "degraded", "missing", "unsupported"):
        return value
    raise ValidationError(f"invalid source state {value!r}")
