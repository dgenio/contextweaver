"""Package-native, deterministic compiler-first adoption proof.

Run after installing ContextWeaver::

    python -m contextweaver.compiler

The proof constructs five heterogeneous *source snapshots*, compiles and
verifies one portable bundle, routes/hydrates without executing a capability,
and demonstrates fail-closed runtime assessment when a required resource loses
its verification digest. Source-specific discovery adapters remain separate
work; this demo does not pretend those adapters executed.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from contextweaver.compiler._json import sha256_hex
from contextweaver.compiler.analysis import analyze_snapshots
from contextweaver.compiler.bundle import build_bundle_from_snapshots, verify_bundle, write_bundle
from contextweaver.compiler.resources import ResourceDescriptor
from contextweaver.compiler.runtime import CompiledAgent
from contextweaver.compiler.sources import CapabilitySourceSnapshot, SourceCoverage
from contextweaver.types import SelectableItem

AGENT_ID = "demo.compiler-first"
QUERY = "draft payment reminder"


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
        source_version="fixture-v1",
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


def build_demo_snapshots(*, degraded_skill_resource: bool = False) -> list[CapabilitySourceSnapshot]:
    """Return five representative source snapshots for the compiler proof."""
    skill_body = b"Draft a concise, polite reminder. Never claim a payment was received."
    skill_resource = ResourceDescriptor(
        resource_id="skill.reminder.instructions",
        uri="fixture://skills/reminder/SKILL.md",
        media_type="text/markdown",
        digest="" if degraded_skill_resource else sha256_hex(skill_body),
        size_bytes=len(skill_body),
        capability_ids=["skill.draft_reminder"],
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
                "skill.draft_reminder",
                name="draft payment reminder",
                description="Draft a polite payment reminder for an unpaid invoice.",
                namespace="skill",
                resource_ids=["skill.reminder.instructions"],
            ),
            resources=[skill_resource],
        ),
        _snapshot(
            "framework.crm",
            "framework",
            _item(
                "crm.lookup_account",
                name="lookup customer account",
                description="Lookup customer account metadata in a framework tool wrapper.",
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

    lines = [
        "ContextWeaver compiler-first proof",
        (
            f"DISCOVER sources={len(snapshots)} types={source_types} "
            f"capabilities={capability_count} discovery=fixture-snapshots"
        ),
        (
            f"ANALYSE trust={preflight.trust_status} "
            f"required_resources={preflight.required_resource_count} "
            f"warnings={len(preflight.warnings)} findings={len(preflight.findings)}"
        ),
    ]

    with tempfile.TemporaryDirectory(prefix="contextweaver-compiler-demo-") as tmp:
        bundle = build_bundle_from_snapshots(AGENT_ID, snapshots)
        bundle_path = write_bundle(bundle, Path(tmp))
        verification = verify_bundle(bundle_path)
        agent = CompiledAgent.load(bundle_path)
        trust_status = bundle.trust.status if bundle.trust else "unverified"
        lines.append(
            f"COMPILE trust={trust_status} bundle_verified={str(verification.ok).lower()} "
            f"capabilities={len(bundle.capabilities)} resources={len(bundle.resources)}"
        )

        route = agent.route(QUERY)
        selected = route.candidate_ids[0]
        if selected != "skill.draft_reminder":
            raise AssertionError(f"compiler demo route drifted: expected skill.draft_reminder, got {selected}")
        lines.append(f"ROUTE selected={selected} shortlist={len(route.candidate_ids)}")

        hydrated = agent.hydrate(selected)
        resource_ids = ",".join(resource.resource_id for resource in hydrated.resources)
        lines.append(
            f"HYDRATE capability={selected} resources={resource_ids} "
            "host_execution=retained"
        )

    degraded = CompiledAgent(
        build_bundle_from_snapshots(
            AGENT_ID,
            build_demo_snapshots(degraded_skill_resource=True),
        )
    ).assess_runtime()
    lines.append(
        f"DEGRADED trust={degraded.status} "
        f"blocked={','.join(degraded.blocked_capability_ids)} "
        f"allowed={len(degraded.allowed_capability_ids)}"
    )
    lines.append(
        "PASS: heterogeneous source snapshots became one portable bundle "
        "without executing a capability."
    )
    return lines


def main() -> None:
    """Print the stable compiler-first proof receipt."""
    print("\n".join(receipt_lines()))


if __name__ == "__main__":
    main()
