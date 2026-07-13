"""Tests for the compiler-first MVP foundation."""

from __future__ import annotations

import shutil
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from contextweaver.compiler import (
    EnrichmentPatch,
    InMemoryResourceResolver,
    ResourceDescriptor,
    ResourceResolution,
    ResourceResolutionRequest,
    SourceCoverage,
    analyze_bundle,
    analyze_snapshots,
    build_bundle_from_snapshots,
    validate_resolution,
    verify_bundle,
    write_bundle,
)
from contextweaver.compiler.bundle import load_bundle
from contextweaver.compiler.runtime import CompiledAgent
from contextweaver.compiler.sources import CapabilitySourceSnapshot
from contextweaver.exceptions import ValidationError
from contextweaver.types import SelectableItem


@contextmanager
def _temp_root() -> Iterator[Path]:
    root = Path("test-output") / "compiler" / uuid.uuid4().hex
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _item(
    item_id: str,
    description: str,
    *,
    resource_ids: list[str] | None = None,
) -> SelectableItem:
    metadata = {"resource_ids": resource_ids or []} if resource_ids else {}
    return SelectableItem(
        id=item_id,
        kind="tool",
        name=item_id.replace(".", " "),
        description=description,
        namespace=item_id.split(".")[0],
        tags=["compiler-test"],
        args_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        metadata=metadata,
    )


def _snapshot() -> CapabilitySourceSnapshot:
    resource = ResourceDescriptor(
        resource_id="docs.api",
        uri="host://docs/api.md",
        media_type="text/markdown",
        digest="d3bd3e215a98a304a1fbfcbfbbe88adb755f7843574408a1b2fc7eb0b03a7c14",
        size_bytes=16,
        capability_ids=["github.search_issues"],
    )
    return CapabilitySourceSnapshot(
        source_id="fixture",
        source_type="fixture",
        source_version="1",
        adapter_id="fixture-adapter",
        adapter_version="1",
        capabilities=[
            _item("calendar.create_event", "Create a calendar event."),
            _item(
                "github.search_issues",
                "Search GitHub issues by keyword and repository.",
                resource_ids=["docs.api"],
            ),
        ],
        resources=[resource],
        coverage=SourceCoverage(
            source_id="fixture",
            capability_ids=["calendar.create_event", "github.search_issues"],
        ),
    )


def test_source_snapshot_digest_is_deterministic() -> None:
    first = _snapshot()
    second = _snapshot()
    second.capabilities = list(reversed(second.capabilities))

    assert first.digest() == second.digest()
    assert first.to_dict()["digest"] == first.digest()


def test_resource_validation_checks_declared_digest_size_and_media_type() -> None:
    descriptor = _snapshot().resources[0]
    matching = ResourceResolution(
        resource_id="docs.api",
        content=b"api docs fixture",
        media_type="text/markdown",
    )
    mismatched = ResourceResolution(
        resource_id="docs.api",
        content=b"wrong",
        media_type="text/markdown",
    )

    assert validate_resolution(descriptor, matching).status == "verified"
    invalid = validate_resolution(descriptor, mismatched)
    assert invalid.status == "invalid"
    assert "digest mismatch" in invalid.findings
    assert "size mismatch" in invalid.findings


def test_in_memory_resource_resolver_rejects_undeclared_resources() -> None:
    descriptor = _snapshot().resources[0]
    resolver = InMemoryResourceResolver(
        [descriptor],
        {"docs.api": b"api docs fixture"},
    )

    resolution = resolver.resolve(ResourceResolutionRequest("docs.api", descriptor))
    assert validate_resolution(descriptor, resolution).ok

    rogue = ResourceDescriptor(resource_id="rogue", uri="host://rogue")
    with pytest.raises(ValidationError, match="was not declared"):
        resolver.resolve(ResourceResolutionRequest("rogue", rogue))


def test_bundle_write_load_and_verify_round_trips() -> None:
    bundle = build_bundle_from_snapshots("agent.fixture", [_snapshot()])

    with _temp_root() as root:
        bundle_path = write_bundle(bundle, root)
        assert bundle_path.name == bundle.bundle_digest()
        assert verify_bundle(bundle_path).ok

        loaded = load_bundle(bundle_path)

    assert loaded.agent_id == "agent.fixture"
    assert [item.id for item in loaded.capabilities] == [
        "calendar.create_event",
        "github.search_issues",
    ]
    assert loaded.bundle_digest() == bundle.bundle_digest()


def test_bundle_verifier_detects_component_tampering() -> None:
    with _temp_root() as root:
        bundle_path = write_bundle(
            build_bundle_from_snapshots("agent.fixture", [_snapshot()]),
            root,
        )
        (bundle_path / "capabilities.json").write_text("[]\n", encoding="utf-8")

        report = verify_bundle(bundle_path)

    assert not report.ok
    assert "component 'capabilities.json' digest mismatch" in report.findings


def test_compiled_agent_routes_and_hydrates_declared_resources() -> None:
    with _temp_root() as root:
        bundle_path = write_bundle(
            build_bundle_from_snapshots("agent.fixture", [_snapshot()]),
            root,
        )
        agent = CompiledAgent.load(bundle_path)

    route = agent.route("search GitHub issues by repository keyword")
    hydrated = agent.hydrate(route.candidate_ids[0])

    assert route.candidate_ids[0] == "github.search_issues"
    assert hydrated.hydration.item.id == "github.search_issues"
    assert [resource.resource_id for resource in hydrated.resources] == ["docs.api"]
    assert hydrated.trust is not None
    assert hydrated.trust.bundle_digest == agent.bundle.bundle_digest()


def test_analysis_reports_snapshot_and_bundle_counts() -> None:
    snapshot = _snapshot()
    with _temp_root() as root:
        bundle_path = write_bundle(
            build_bundle_from_snapshots("agent.fixture", [snapshot]),
            root,
        )
        bundle_report = analyze_bundle(bundle_path)

    snapshot_report = analyze_snapshots("agent.fixture", [snapshot])

    assert snapshot_report.trust_status == "unverified"
    assert bundle_report.capability_count == 2
    assert bundle_report.resource_count == 1
    assert bundle_report.required_resource_count == 1
    assert bundle_report.trust_status == "verified"


def test_enrichment_patch_round_trips() -> None:
    patch = EnrichmentPatch(
        capability_id="github.search_issues",
        field_path="description",
        before="old",
        after="new",
        state="accepted",
        provenance={"reviewer": "maintainer"},
    )

    assert EnrichmentPatch.from_dict(patch.to_dict()).to_dict() == patch.to_dict()


def test_bundle_trust_summary_reflects_source_warnings_and_resource_gaps() -> None:
    snapshot = _snapshot()
    snapshot.warnings.append("source used fallback metadata")
    snapshot.resources[0].digest = ""

    bundle = build_bundle_from_snapshots("agent.fixture", [snapshot])

    assert bundle.trust is not None
    assert bundle.trust.status == "unverified"
    assert "source used fallback metadata" in bundle.trust.warnings
    assert "resource 'docs.api' has no declared digest" in bundle.trust.warnings
