"""Trust metadata for compiled bundles and runtime checks."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from contextweaver.exceptions import ValidationError

TrustStatus = Literal[
    "verified",
    "verified_with_warnings",
    "degraded",
    "unverified",
    "invalid",
]

TRUST_STATUS_ORDER: dict[TrustStatus, int] = {
    "verified": 0,
    "verified_with_warnings": 1,
    "degraded": 2,
    "unverified": 3,
    "invalid": 4,
}


@dataclass
class TrustSummary:
    """Recomputable trust projection stored in a compiled bundle manifest."""

    bundle_digest: str
    status: TrustStatus
    source_count: int = 0
    capability_count: int = 0
    resource_count: int = 0
    warnings: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "bundle_digest": self.bundle_digest,
            "status": self.status,
            "source_count": self.source_count,
            "capability_count": self.capability_count,
            "resource_count": self.resource_count,
            "warnings": list(self.warnings),
            "findings": list(self.findings),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TrustSummary:
        """Deserialise from a JSON-compatible mapping."""
        return cls(
            bundle_digest=str(data["bundle_digest"]),
            status=_trust_status(data["status"]),
            source_count=int(data.get("source_count", 0)),
            capability_count=int(data.get("capability_count", 0)),
            resource_count=int(data.get("resource_count", 0)),
            warnings=[str(v) for v in data.get("warnings", [])],
            findings=[str(v) for v in data.get("findings", [])],
        )


@dataclass
class RuntimeTrustAssessment:
    """Runtime trust check that never mutates compiled bundle identity."""

    bundle_digest: str
    status: TrustStatus
    checked_at: str = ""
    allowed_capability_ids: list[str] = field(default_factory=list)
    blocked_capability_ids: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "bundle_digest": self.bundle_digest,
            "status": self.status,
            "checked_at": self.checked_at,
            "allowed_capability_ids": list(self.allowed_capability_ids),
            "blocked_capability_ids": list(self.blocked_capability_ids),
            "findings": list(self.findings),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RuntimeTrustAssessment:
        """Deserialise from a JSON-compatible mapping."""
        return cls(
            bundle_digest=str(data["bundle_digest"]),
            status=_trust_status(data["status"]),
            checked_at=str(data.get("checked_at", "")),
            allowed_capability_ids=[str(v) for v in data.get("allowed_capability_ids", [])],
            blocked_capability_ids=[str(v) for v in data.get("blocked_capability_ids", [])],
            findings=[str(v) for v in data.get("findings", [])],
        )


def worst_trust_status(statuses: list[TrustStatus]) -> TrustStatus:
    """Return the least-trusted status from *statuses*."""
    if not statuses:
        return "unverified"
    return max(statuses, key=lambda status: TRUST_STATUS_ORDER[status])


def _trust_status(value: object) -> TrustStatus:
    if value in TRUST_STATUS_ORDER:
        return value
    raise ValidationError(f"invalid trust status {value!r}")
