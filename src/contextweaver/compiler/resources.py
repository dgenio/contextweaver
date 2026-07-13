"""Resource descriptors and host-provided resolution contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from contextweaver.compiler._json import sha256_hex
from contextweaver.exceptions import ValidationError

ResourceRequirement = Literal["required", "optional"]
ResolutionStatus = Literal["resolved", "missing", "error"]
ResourceVerificationStatus = Literal[
    "verified",
    "verified_with_warnings",
    "degraded",
    "unverified",
    "invalid",
]


@dataclass
class ResourceDescriptor:
    """Declared external resource required by one or more capabilities.

    ContextWeaver records identity and verification constraints only; hosts own
    fetching, credentials, IAM, and side effects.
    """

    resource_id: str
    uri: str
    requirement: ResourceRequirement = "required"
    media_type: str = ""
    digest: str = ""
    size_bytes: int | None = None
    capability_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "resource_id": self.resource_id,
            "uri": self.uri,
            "requirement": self.requirement,
            "media_type": self.media_type,
            "digest": self.digest,
            "size_bytes": self.size_bytes,
            "capability_ids": list(self.capability_ids),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ResourceDescriptor:
        """Deserialise from a JSON-compatible mapping."""
        return cls(
            resource_id=str(data["resource_id"]),
            uri=str(data["uri"]),
            requirement=_resource_requirement(data.get("requirement", "required")),
            media_type=str(data.get("media_type", "")),
            digest=str(data.get("digest", "")),
            size_bytes=(int(data["size_bytes"]) if data.get("size_bytes") is not None else None),
            capability_ids=[str(v) for v in data.get("capability_ids", [])],
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class ResourceResolutionRequest:
    """Host-facing request to resolve a declared resource."""

    resource_id: str
    descriptor: ResourceDescriptor


@dataclass
class ResourceResolution:
    """Host-supplied resource evidence.

    ``content`` is optional and never written into compiler bundles. When
    provided, its digest and size are used as verification evidence.
    """

    resource_id: str
    status: ResolutionStatus = "resolved"
    content: bytes | None = None
    digest: str = ""
    size_bytes: int | None = None
    media_type: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def observed_digest(self) -> str:
        """Return a digest from content or host-provided evidence."""
        if self.content is not None:
            return sha256_hex(self.content)
        return self.digest

    def observed_size(self) -> int | None:
        """Return a size from content or host-provided evidence."""
        if self.content is not None:
            return len(self.content)
        return self.size_bytes

    def to_dict(self) -> dict[str, Any]:
        """Serialise evidence without embedding raw resource bytes."""
        return {
            "resource_id": self.resource_id,
            "status": self.status,
            "digest": self.observed_digest(),
            "size_bytes": self.observed_size(),
            "media_type": self.media_type,
            "evidence": dict(self.evidence),
            "error": self.error,
        }


@dataclass
class ResourceValidation:
    """Verification result for a resolved resource."""

    resource_id: str
    status: ResourceVerificationStatus
    findings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """``True`` when the resource is safe to use for required closure."""
        return self.status in ("verified", "verified_with_warnings")

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "resource_id": self.resource_id,
            "status": self.status,
            "findings": list(self.findings),
        }


class ResourceResolver(Protocol):
    """Host-owned resolver for declared resources."""

    def resolve(self, request: ResourceResolutionRequest) -> ResourceResolution:
        """Resolve *request* and return verification evidence."""


class InMemoryResourceResolver:
    """Deterministic host resolver for tests and offline fixtures."""

    def __init__(
        self,
        descriptors: list[ResourceDescriptor],
        contents: Mapping[str, bytes] | None = None,
    ) -> None:
        self._descriptors = {d.resource_id: d for d in descriptors}
        self._contents = dict(contents or {})

    def resolve(self, request: ResourceResolutionRequest) -> ResourceResolution:
        """Resolve only resources declared in the descriptor set."""
        expected = self._descriptors.get(request.resource_id)
        if expected is None:
            raise ValidationError(
                f"resource {request.resource_id!r} was not declared in this resolver"
            )
        if expected.to_dict() != request.descriptor.to_dict():
            raise ValidationError(
                f"resource {request.resource_id!r} descriptor does not match declaration"
            )
        content = self._contents.get(request.resource_id)
        if content is None:
            return ResourceResolution(
                resource_id=request.resource_id,
                status="missing",
                error="resource content was not supplied by the host",
            )
        return ResourceResolution(
            resource_id=request.resource_id,
            content=content,
            media_type=expected.media_type,
        )


def validate_resolution(
    descriptor: ResourceDescriptor,
    resolution: ResourceResolution,
) -> ResourceValidation:
    """Validate host resource evidence against a declared descriptor."""
    findings: list[str] = []
    if resolution.resource_id != descriptor.resource_id:
        return ResourceValidation(
            descriptor.resource_id,
            "invalid",
            [
                f"resolved id {resolution.resource_id!r} does not match "
                f"declared id {descriptor.resource_id!r}"
            ],
        )
    if resolution.status != "resolved":
        status: ResourceVerificationStatus = (
            "degraded" if descriptor.requirement == "optional" else "invalid"
        )
        return ResourceValidation(descriptor.resource_id, status, [resolution.error])

    observed_digest = resolution.observed_digest()
    observed_size = resolution.observed_size()
    if descriptor.digest and observed_digest != descriptor.digest:
        findings.append("digest mismatch")
    if descriptor.size_bytes is not None and observed_size != descriptor.size_bytes:
        findings.append("size mismatch")
    if descriptor.media_type and resolution.media_type != descriptor.media_type:
        findings.append("media type mismatch")
    if findings:
        return ResourceValidation(descriptor.resource_id, "invalid", findings)
    if descriptor.digest:
        return ResourceValidation(descriptor.resource_id, "verified", [])
    if descriptor.size_bytes is not None or descriptor.media_type:
        return ResourceValidation(
            descriptor.resource_id,
            "verified_with_warnings",
            ["resource has no declared digest"],
        )
    return ResourceValidation(
        descriptor.resource_id,
        "unverified",
        ["resource descriptor has no digest, size, or media constraint"],
    )


def _resource_requirement(value: object) -> ResourceRequirement:
    if value in ("required", "optional"):
        return value  # type: ignore[return-value]
    raise ValidationError(f"invalid resource requirement {value!r}")
