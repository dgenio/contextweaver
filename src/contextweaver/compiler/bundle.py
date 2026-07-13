"""Compiled-agent bundle writer, loader, and verifier."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from contextweaver.compiler._bundle_dedupe import dedupe_capabilities, dedupe_resources
from contextweaver.compiler._bundle_trust import summarize_trust_inputs
from contextweaver.compiler._json import digest_json, pretty_json, sha256_hex
from contextweaver.compiler.enrichment import EnrichmentPatch
from contextweaver.compiler.resources import ResourceDescriptor
from contextweaver.compiler.sources import CapabilitySourceSnapshot
from contextweaver.compiler.trust import TrustSummary
from contextweaver.exceptions import ValidationError
from contextweaver.types import SelectableItem

COMPILED_BUNDLE_VERSION = "contextweaver.compiler.bundle.v1"
COMPONENT_PATHS = ("agent.json", "capabilities.json", "resources.json", "lock.json")


@dataclass
class BundleComponent:
    """Digest and size metadata for one bundle component."""

    path: str
    digest: str
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {"path": self.path, "digest": self.digest, "size_bytes": self.size_bytes}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> BundleComponent:
        """Deserialise from a JSON-compatible mapping."""
        return cls(
            path=str(data["path"]),
            digest=str(data["digest"]),
            size_bytes=int(data["size_bytes"]),
        )


@dataclass
class BundleVerification:
    """Verifier result for an on-disk compiled bundle."""

    ok: bool
    bundle_digest: str = ""
    findings: list[str] = field(default_factory=list)


@dataclass
class BundleManifest:
    """Manifest for a content-addressed compiled bundle directory."""

    bundle_version: str
    bundle_digest: str
    components: list[BundleComponent]
    trust: TrustSummary

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "bundle_version": self.bundle_version,
            "bundle_digest": self.bundle_digest,
            "components": [component.to_dict() for component in self.components],
            "trust": self.trust.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> BundleManifest:
        """Deserialise from a JSON-compatible mapping."""
        return cls(
            bundle_version=str(data["bundle_version"]),
            bundle_digest=str(data["bundle_digest"]),
            components=[BundleComponent.from_dict(dict(raw)) for raw in data.get("components", [])],
            trust=TrustSummary.from_dict(dict(data["trust"])),
        )


@dataclass
class CompiledBundle:
    """In-memory representation of a compiled-agent bundle."""

    agent_id: str
    name: str = ""
    version: str = ""
    capabilities: list[SelectableItem] = field(default_factory=list)
    resources: list[ResourceDescriptor] = field(default_factory=list)
    sources: list[CapabilitySourceSnapshot] = field(default_factory=list)
    enrichments: list[EnrichmentPatch] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    trust: TrustSummary | None = None

    def component_payloads(self) -> dict[str, Any]:
        """Return deterministic component payloads keyed by file path."""
        return {
            "agent.json": {
                "agent_id": self.agent_id,
                "name": self.name,
                "version": self.version,
                "metadata": dict(self.metadata),
            },
            "capabilities.json": [
                item.to_dict() for item in sorted(self.capabilities, key=lambda it: it.id)
            ],
            "resources.json": [
                res.to_dict() for res in sorted(self.resources, key=lambda res: res.resource_id)
            ],
            "lock.json": {
                "sources": [
                    source.to_dict()
                    for source in sorted(self.sources, key=lambda source: source.source_id)
                ],
                "enrichments": [
                    patch.to_dict()
                    for patch in sorted(
                        self.enrichments,
                        key=lambda patch: (patch.capability_id, patch.field_path),
                    )
                ],
            },
        }

    def bundle_digest(self) -> str:
        """Return the logical digest over bundle components."""
        components = _components_for_payloads(self.component_payloads())
        return _digest_components(components)

    def manifest(self) -> BundleManifest:
        """Return a manifest with recomputed component and trust metadata."""
        payloads = self.component_payloads()
        components = _components_for_payloads(payloads)
        bundle_digest = _digest_components(components)
        trust = self.trust or TrustSummary(
            bundle_digest=bundle_digest,
            status="verified",
            source_count=len(self.sources),
            capability_count=len(self.capabilities),
            resource_count=len(self.resources),
        )
        if trust.bundle_digest != bundle_digest:
            trust = TrustSummary(
                bundle_digest=bundle_digest,
                status=trust.status,
                source_count=trust.source_count,
                capability_count=trust.capability_count,
                resource_count=trust.resource_count,
                warnings=list(trust.warnings),
                findings=list(trust.findings),
            )
        return BundleManifest(COMPILED_BUNDLE_VERSION, bundle_digest, components, trust)


def build_bundle_from_snapshots(
    agent_id: str,
    snapshots: list[CapabilitySourceSnapshot],
    *,
    name: str = "",
    version: str = "",
    metadata: Mapping[str, Any] | None = None,
    enrichments: list[EnrichmentPatch] | None = None,
) -> CompiledBundle:
    """Build a deterministic compiled bundle from source snapshots."""
    capabilities = dedupe_capabilities(snapshots)
    resources = dedupe_resources(snapshots)
    bundle = CompiledBundle(
        agent_id=agent_id,
        name=name,
        version=version,
        capabilities=capabilities,
        resources=resources,
        sources=list(snapshots),
        enrichments=list(enrichments or []),
        metadata=dict(metadata or {}),
    )
    digest = bundle.bundle_digest()
    status, warnings, findings = summarize_trust_inputs(snapshots, resources)
    bundle.trust = TrustSummary(
        bundle_digest=digest,
        status=status,
        source_count=len(snapshots),
        capability_count=len(capabilities),
        resource_count=len(resources),
        warnings=warnings,
        findings=findings,
    )
    return bundle


def write_bundle(bundle: CompiledBundle, root: str | Path) -> Path:
    """Write *bundle* under a content-addressed directory and return its path."""
    target = Path(root) / bundle.bundle_digest()
    target.mkdir(parents=True, exist_ok=True)
    payloads = bundle.component_payloads()
    for path in COMPONENT_PATHS:
        (target / path).write_text(pretty_json(payloads[path]), encoding="utf-8", newline="\n")
    (target / "manifest.json").write_text(
        pretty_json(bundle.manifest().to_dict()),
        encoding="utf-8",
        newline="\n",
    )
    return target


def load_bundle(path: str | Path, *, verify: bool = True) -> CompiledBundle:
    """Load a compiled bundle from *path*."""
    bundle_path = Path(path)
    if verify:
        report = verify_bundle(bundle_path)
        if not report.ok:
            raise ValidationError(f"compiled bundle verification failed: {report.findings}")
    agent = cast(dict[str, Any], _read_json(bundle_path / "agent.json"))
    capabilities = cast(list[Any], _read_json(bundle_path / "capabilities.json"))
    resources = cast(list[Any], _read_json(bundle_path / "resources.json"))
    lock = cast(dict[str, Any], _read_json(bundle_path / "lock.json"))
    manifest = BundleManifest.from_dict(
        cast(dict[str, Any], _read_json(bundle_path / "manifest.json"))
    )
    return CompiledBundle(
        agent_id=str(agent["agent_id"]),
        name=str(agent.get("name", "")),
        version=str(agent.get("version", "")),
        capabilities=[SelectableItem.from_dict(dict(raw)) for raw in capabilities],
        resources=[ResourceDescriptor.from_dict(dict(raw)) for raw in resources],
        sources=[CapabilitySourceSnapshot.from_dict(dict(raw)) for raw in lock.get("sources", [])],
        enrichments=[EnrichmentPatch.from_dict(dict(raw)) for raw in lock.get("enrichments", [])],
        metadata=dict(agent.get("metadata", {})),
        trust=manifest.trust,
    )


def verify_bundle(path: str | Path) -> BundleVerification:
    """Verify component hashes, sizes, and logical digest for *path*."""
    bundle_path = Path(path)
    findings: list[str] = []
    try:
        manifest = BundleManifest.from_dict(
            cast(dict[str, Any], _read_json(bundle_path / "manifest.json"))
        )
    except (OSError, KeyError, TypeError, json.JSONDecodeError, ValidationError) as exc:
        return BundleVerification(False, findings=[f"invalid manifest: {exc}"])
    expected_paths = set(COMPONENT_PATHS)
    components_by_path = {component.path: component for component in manifest.components}
    for component in manifest.components:
        if component.path not in expected_paths:
            findings.append(f"manifest references unexpected component {component.path!r}")
    actual_components: list[BundleComponent] = []
    for component_path in COMPONENT_PATHS:
        component = components_by_path.get(component_path)
        if component is None:
            findings.append(f"manifest missing component {component_path!r}")
            continue
        try:
            data = (bundle_path / component_path).read_bytes()
        except OSError as exc:
            findings.append(f"cannot read component {component_path!r}: {exc}")
            continue
        actual = BundleComponent(component_path, sha256_hex(data), len(data))
        actual_components.append(actual)
        if actual.digest != component.digest:
            findings.append(f"component {component_path!r} digest mismatch")
        if actual.size_bytes != component.size_bytes:
            findings.append(f"component {component_path!r} size mismatch")
    logical = _digest_components(actual_components)
    if logical != manifest.bundle_digest:
        findings.append("logical bundle digest mismatch")
    if bundle_path.name != manifest.bundle_digest:
        findings.append("bundle directory name does not match manifest digest")
    if manifest.trust.bundle_digest != manifest.bundle_digest:
        findings.append("trust summary bundle digest mismatch")
    return BundleVerification(not findings, manifest.bundle_digest, findings)


def _components_for_payloads(payloads: Mapping[str, Any]) -> list[BundleComponent]:
    components: list[BundleComponent] = []
    for path in COMPONENT_PATHS:
        data = pretty_json(payloads[path]).encode("utf-8")
        components.append(BundleComponent(path, sha256_hex(data), len(data)))
    return components


def _digest_components(components: list[BundleComponent]) -> str:
    return digest_json(
        {"components": [c.to_dict() for c in sorted(components, key=lambda c: c.path)]}
    )


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))
