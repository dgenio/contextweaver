"""Offline capability compiler foundation."""

from __future__ import annotations

from contextweaver.compiler.analysis import (
    ANALYSIS_REPORT_VERSION,
    AnalysisReport,
    analyze_bundle,
    analyze_snapshots,
)
from contextweaver.compiler.bundle import (
    COMPILED_BUNDLE_VERSION,
    BundleComponent,
    BundleManifest,
    BundleVerification,
    CompiledBundle,
    build_bundle_from_snapshots,
    load_bundle,
    verify_bundle,
    write_bundle,
)
from contextweaver.compiler.enrichment import (
    ENRICHMENT_PATCH_VERSION,
    EnrichmentPatch,
)
from contextweaver.compiler.resources import (
    InMemoryResourceResolver,
    ResourceDescriptor,
    ResourceResolution,
    ResourceResolutionRequest,
    ResourceResolver,
    ResourceValidation,
    validate_resolution,
)
from contextweaver.compiler.runtime import CompiledAgent, CompiledHydrationResult
from contextweaver.compiler.sources import (
    CapabilitySourceAdapter,
    CapabilitySourceSnapshot,
    SourceCoverage,
)
from contextweaver.compiler.trust import (
    TRUST_STATUS_ORDER,
    RuntimeTrustAssessment,
    TrustSummary,
    worst_trust_status,
)

__all__ = [
    "ANALYSIS_REPORT_VERSION",
    "COMPILED_BUNDLE_VERSION",
    "ENRICHMENT_PATCH_VERSION",
    "TRUST_STATUS_ORDER",
    "AnalysisReport",
    "BundleComponent",
    "BundleManifest",
    "BundleVerification",
    "CapabilitySourceAdapter",
    "CapabilitySourceSnapshot",
    "CompiledAgent",
    "CompiledBundle",
    "CompiledHydrationResult",
    "EnrichmentPatch",
    "InMemoryResourceResolver",
    "ResourceDescriptor",
    "ResourceResolution",
    "ResourceResolutionRequest",
    "ResourceResolver",
    "ResourceValidation",
    "RuntimeTrustAssessment",
    "SourceCoverage",
    "TrustSummary",
    "analyze_bundle",
    "analyze_snapshots",
    "build_bundle_from_snapshots",
    "load_bundle",
    "validate_resolution",
    "verify_bundle",
    "worst_trust_status",
    "write_bundle",
]
