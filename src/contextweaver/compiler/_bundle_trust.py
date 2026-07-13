"""Private trust aggregation helpers for compiler bundles."""

from __future__ import annotations

from contextweaver.compiler.resources import ResourceDescriptor
from contextweaver.compiler.sources import CapabilitySourceSnapshot
from contextweaver.compiler.trust import TrustStatus, worst_trust_status


def summarize_trust_inputs(
    snapshots: list[CapabilitySourceSnapshot],
    resources: list[ResourceDescriptor],
) -> tuple[TrustStatus, list[str], list[str]]:
    """Return bundle trust status plus warnings and findings."""
    statuses: list[TrustStatus] = []
    warnings: list[str] = []
    findings: list[str] = []
    for snapshot in snapshots:
        warnings.extend(snapshot.warnings)
        findings.extend(snapshot.unsupported)
        findings.extend(snapshot.semantic_loss)
        if snapshot.warnings:
            statuses.append("verified_with_warnings")
        if snapshot.unsupported or snapshot.semantic_loss:
            statuses.append("degraded")
        if snapshot.coverage is not None:
            warnings.extend(snapshot.coverage.warnings)
            findings.extend(snapshot.coverage.semantic_loss)
            if snapshot.coverage.state == "degraded":
                statuses.append("degraded")
            if snapshot.coverage.state in ("missing", "unsupported"):
                statuses.append(
                    "invalid" if snapshot.coverage.requirement == "required" else "degraded"
                )
    for resource in resources:
        if not resource.digest:
            warnings.append(f"resource {resource.resource_id!r} has no declared digest")
            statuses.append(
                "unverified" if resource.requirement == "required" else "verified_with_warnings"
            )
    return worst_trust_status(statuses or ["verified"]), warnings, findings
