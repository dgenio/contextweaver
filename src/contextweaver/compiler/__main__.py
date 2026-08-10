"""Package-native, deterministic compiler-first adoption proof.

Run after installing ContextWeaver::

    python -m contextweaver.compiler

The proof constructs five heterogeneous *source snapshots*, detects one
cross-source identity ambiguity, compiles and verifies one portable bundle,
evaluates a phase-bounded route, hydrates only the selected capability, and
demonstrates fail-closed runtime assessment when a required resource loses its
verification digest. Source-specific discovery adapters remain separate work;
this demo does not pretend those adapters executed.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from contextweaver.compiler._json import sha256_hex
from contextweaver.compiler.analysis import analyze_snapshots
from contextweaver.compiler.bundle import (
    COMPILED_BUNDLE_VERSION,
    build_bundle_from_snapshots,
    write_bundle,
)
from contextweaver.compiler.resources import ResourceDescriptor
from contextweaver.compiler.runtime import CompiledAgent
from contextweaver.compiler.sources import CapabilitySourceSnapshot, SourceCoverage
from contextweaver.exceptions import ValidationError
from contextweaver.routing.collision import analyze_collisions
from contextweaver.types import SelectableItem

AGENT_ID = "demo.compiler-first"
ARTIFACT_VERSION = "fixture-v1"
EVAL_FIXTURE_ID = "compiler-demo-eval-v1"
PHASE_ID = "draft-reminder"
PHASE_NAMESPACE = "skill"
QUERY = "draft payment reminder"
EXPECTED_CAPABILITY_ID = "skill.draft_reminder"


def _item(
    item_id: str,
    *,
    name: str,
    description: str,
    namespace: str,
    resource_ids: list[str] | None = None,
) -> SelectableItem:
    """Build one deterministic demo capability."""
    metadata = {"resource_ids": list(resource_ids or [])} if resource_ids else {}
    return SelectableItem(
        id=item_id,
        kind="tool",
        name=name,
        description=description,
        namespace=namespace,
        tags=["compiler-demo"],
        args_schema={"type": "object", "properties": {}},
        metadata=metadata,
    )


def _snapshot(
    source_id: str,
    source_type: str,
    capability: SelectableItem,
    *,
    resources: list[ResourceDescriptor] | None = None,
) -> CapabilitySourceSnapshot:
    """Build one explicit heterogeneous-source snapshot."""
    return CapabilitySourceSnapshot(
        source_id=source_id,
        source_type=source_type,
        source_version=ARTIFACT_VERSION,
        adapter_id=f"demo-{source_type}-snapshot",
        adapter_version="1",
        capabilities=[capability],
        resources=list(resources or []),
        coverage=SourceCoverage(
            source_id=source_id,
            capability_ids=[capability.id],
        ),
        metadata={"demo_fixture": True, "discovery_executed": False},
    )


def build_demo_snapshots(
    *, degraded_skill_resource: bool = False
) -> list[CapabilitySourceSnapshot]:
    """Return five representative source snapshots for the compiler proof."""
    skill_body = b"Draft a concise, polite reminder. Never claim a payment was received."
    skill_resource = ResourceDescriptor(
        resource_id="skill.reminder.instructions",
        uri="fixture://skills/reminder/SKILL.md",
        media_type="text/markdown",
        digest="" if degraded_skill_resource else sha256_hex(skill_body),
        size_bytes=len(skill_body),
        capability_ids=[EXPECTED_CAPABILITY_ID],
    )

    return [
        _snapshot(
            "mcp.helpdesk",
            "mcp",
            _item(
                "mcp.search_tickets",
                name="search support tickets",
                description="Search support tickets by account, status, and keyword.",
                namespace="mcp",
            ),
        ),
        _snapshot(
            "openapi.billing",
            "openapi",
            _item(
                "billing.get_invoice",
                name="get invoice",
                description="Fetch invoice status and balance by invoice identifier.",
                namespace="billing",
            ),
        ),
        _snapshot(
            "skill.reminder",
            "agent-skill",
            _item(
                EXPECTED_CAPABILITY_ID,
                name="draft payment reminder",
                description="Draft a polite payment reminder for an unpaid invoice.",
                namespace=PHASE_NAMESPACE,
                resource_ids=["skill.reminder.instructions"],
            ),
            resources=[skill_resource],
        ),
        _snapshot(
            "framework.crm",
            "framework",
            _item(
                "crm.draft_payment_reminder",
                name="draft payment reminder",
                description="Draft a payment reminder using a framework-native tool object.",
                namespace="crm",
            ),
        ),
        _snapshot(
            "a2a.notifications",
            "a2a",
            _item(
                "a2a.notify_customer",
                name="send customer notification",
                description="Delegate an approved customer notification to a remote agent.",
                namespace="a2a",
            ),
        ),
    ]


def receipt_lines() -> list[str]:
    """Execute the compiler proof and return its stable semantic receipt."""
    snapshots = build_demo_snapshots()
    source_types = ",".join(sorted(snapshot.source_type for snapshot in snapshots))
    capability_count = sum(len(snapshot.capabilities) for snapshot in snapshots)
    preflight = analyze_snapshots(AGENT_ID, snapshots)
    collisions = analyze_collisions(
        [capability for snapshot in snapshots for capability in snapshot.capabilities]
    )
    ambiguities = [
        finding
        for finding in collisions.findings
        if finding.kind in ("exact_name", "near_name")
    ]
    if len(ambiguities) != 1:
        raise ValidationError(
            f"compiler demo ambiguity drifted: expected 1, got {len(ambiguities)}"
        )

    lines = [
        "ContextWeaver compiler-first proof",
        (
            "SURFACE mcp+openapi+agent-skill+framework+a2a -> compile-once "
            "-> evaluate -> portable-bundle -> phase-bounded-hydration"
        ),
        (
            f"DISCOVER sources={len(snapshots)} types={source_types} "
            f"capabilities={capability_count} discovery=fixture-snapshots"
        ),
        (
            f"ANALYSE trust={preflight.trust_status} "
            f"required_resources={preflight.required_resource_count} "
            f"warnings={len(preflight.warnings)} findings={len(preflight.findings)} "
            f"ambiguities={len(ambiguities)}"
        ),
    ]

    with tempfile.TemporaryDirectory(prefix="contextweaver-compiler-demo-") as tmp:
        bundle = build_bundle_from_snapshots(
            AGENT_ID,
            snapshots,
            version=ARTIFACT_VERSION,
        )
        bundle_path = write_bundle(bundle, Path(tmp))
        # CompiledAgent.load verifies the on-disk bundle by default. Reuse that
        # single verified load for the runtime proof rather than verifying twice.
        agent = CompiledAgent.load(bundle_path)
        assessment = agent.assess_runtime()
        trust_status = bundle.trust.status if bundle.trust else "unverified"
        lines.append(
            f"COMPILE trust={trust_status} bundle_version={COMPILED_BUNDLE_VERSION} "
            f"artifact_version={ARTIFACT_VERSION} bundle_id={bundle.bundle_digest()[:12]} "
            f"bundle_verified=true capabilities={len(bundle.capabilities)} "
            f"resources={len(bundle.resources)}"
        )

        route = agent.route(QUERY, allowed_namespaces={PHASE_NAMESPACE})
        if not route.candidate_ids:
            raise ValidationError("compiler demo route drifted: shortlist is empty")
        selected = route.candidate_ids[0]
        gate_passed = (
            selected == EXPECTED_CAPABILITY_ID
            and selected in assessment.allowed_capability_ids
            and len(route.candidate_ids) == 1
        )
        if not gate_passed:
            raise ValidationError(
                "compiler demo evaluation drifted: expected one allowed skill capability, "
                f"got {route.candidate_ids}"
            )
        lines.append(
            f"EVALUATE gate=PASS fixture={EVAL_FIXTURE_ID} cases=1 "
            f"phase={PHASE_ID} allowed_namespace={PHASE_NAMESPACE} selected={selected}"
        )
        lines.append(
            f"ROUTE selected={selected} shortlist={len(route.candidate_ids)} "
            f"phase={PHASE_ID}"
        )

        hydrated = agent.hydrate(selected)
        resource_ids = ",".join(resource.resource_id for resource in hydrated.resources)
        lines.append(
            f"HYDRATE capability={selected} resources={resource_ids} "
            "exposed_capabilities=1 host_execution=retained"
        )

    degraded = CompiledAgent(
        build_bundle_from_snapshots(
            AGENT_ID,
            build_demo_snapshots(degraded_skill_resource=True),
            version=ARTIFACT_VERSION,
        )
    ).assess_runtime()
    if degraded.status != "unverified" or degraded.blocked_capability_ids != [
        EXPECTED_CAPABILITY_ID
    ]:
        raise ValidationError(
            "compiler demo degraded path drifted: "
            f"status={degraded.status} blocked={degraded.blocked_capability_ids}"
        )
    lines.append(
        f"DEGRADED trust={degraded.status} "
        f"blocked={','.join(degraded.blocked_capability_ids)} "
        f"allowed={len(degraded.allowed_capability_ids)} "
        "remediation=restore-required-resource-digest"
    )
    lines.append(
        f"REMEDIATE trust={assessment.status} "
        f"blocked={len(assessment.blocked_capability_ids)} action=restore-digest"
    )
    lines.append(
        "PASS: one evaluated portable bundle exposed only the selected phase resource; "
        "no capability was executed."
    )
    return lines


def main() -> None:
    """Print the stable compiler-first proof receipt."""
    print("\n".join(receipt_lines()))


if __name__ == "__main__":
    main()
