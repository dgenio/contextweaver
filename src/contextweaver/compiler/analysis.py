"""Pre-compilation and bundle analysis reports."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from contextweaver.compiler.bundle import CompiledBundle, load_bundle
from contextweaver.compiler.sources import CapabilitySourceSnapshot
from contextweaver.compiler.trust import TrustStatus, _trust_status


@dataclass
class AnalysisReport:
    """Compact compiler analysis report for previews and CI checks."""

    agent_id: str
    source_count: int = 0
    capability_count: int = 0
    resource_count: int = 0
    required_resource_count: int = 0
    optional_resource_count: int = 0
    trust_status: TrustStatus = "unverified"
    warnings: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "agent_id": self.agent_id,
            "source_count": self.source_count,
            "capability_count": self.capability_count,
            "resource_count": self.resource_count,
            "required_resource_count": self.required_resource_count,
            "optional_resource_count": self.optional_resource_count,
            "trust_status": self.trust_status,
            "warnings": list(self.warnings),
            "findings": list(self.findings),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AnalysisReport:
        """Deserialise from a JSON-compatible mapping."""
        return cls(
            agent_id=str(data["agent_id"]),
            source_count=int(data.get("source_count", 0)),
            capability_count=int(data.get("capability_count", 0)),
            resource_count=int(data.get("resource_count", 0)),
            required_resource_count=int(data.get("required_resource_count", 0)),
            optional_resource_count=int(data.get("optional_resource_count", 0)),
            trust_status=_trust_status(data.get("trust_status", "unverified")),
            warnings=[str(v) for v in data.get("warnings", [])],
            findings=[str(v) for v in data.get("findings", [])],
        )


def analyze_bundle(bundle_or_path: CompiledBundle | str | Path) -> AnalysisReport:
    """Analyze a compiled bundle object or on-disk bundle path."""
    bundle = (
        load_bundle(bundle_or_path) if isinstance(bundle_or_path, (str, Path)) else bundle_or_path
    )
    required = sum(1 for resource in bundle.resources if resource.requirement == "required")
    optional = sum(1 for resource in bundle.resources if resource.requirement == "optional")
    warnings: list[str] = []
    findings: list[str] = []
    for source in bundle.sources:
        warnings.extend(source.warnings)
        findings.extend(source.unsupported)
        findings.extend(source.semantic_loss)
    trust_status: TrustStatus = bundle.trust.status if bundle.trust else "unverified"
    if bundle.trust:
        warnings.extend(bundle.trust.warnings)
        findings.extend(bundle.trust.findings)
    return AnalysisReport(
        agent_id=bundle.agent_id,
        source_count=len(bundle.sources),
        capability_count=len(bundle.capabilities),
        resource_count=len(bundle.resources),
        required_resource_count=required,
        optional_resource_count=optional,
        trust_status=trust_status,
        warnings=warnings,
        findings=findings,
    )


def analyze_snapshots(
    agent_id: str,
    snapshots: list[CapabilitySourceSnapshot],
) -> AnalysisReport:
    """Analyze source snapshots before writing a compiled bundle."""
    capabilities = {item.id for snapshot in snapshots for item in snapshot.capabilities}
    resources = [resource for snapshot in snapshots for resource in snapshot.resources]
    warnings = [warning for snapshot in snapshots for warning in snapshot.warnings]
    findings = [
        finding
        for snapshot in snapshots
        for finding in [*snapshot.unsupported, *snapshot.semantic_loss]
    ]
    return AnalysisReport(
        agent_id=agent_id,
        source_count=len(snapshots),
        capability_count=len(capabilities),
        resource_count=len(resources),
        required_resource_count=sum(1 for res in resources if res.requirement == "required"),
        optional_resource_count=sum(1 for res in resources if res.requirement == "optional"),
        trust_status="unverified",
        warnings=warnings,
        findings=findings,
    )
