"""Regression tests for the package-native compiler-first adoption proof."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from contextweaver.compiler.__main__ import (
    EXPECTED_CAPABILITY_ID,
    PHASE_NAMESPACE,
    build_demo_snapshots,
    receipt_lines,
)
from contextweaver.routing.collision import analyze_collisions

EXPECTED = Path(__file__).parent / "fixtures" / "compiler_demo_expected.txt"


def _run_module(*flags: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *flags, "-m", "contextweaver.compiler"],
        check=True,
        capture_output=True,
        text=True,
    )


def test_compiler_demo_receipt_is_stable() -> None:
    actual = "\n".join(receipt_lines()) + "\n"
    assert actual == EXPECTED.read_text(encoding="utf-8")


def test_compiler_demo_exact_module_command_matches_receipt() -> None:
    completed = _run_module()
    assert completed.stderr == ""
    assert completed.stdout == EXPECTED.read_text(encoding="utf-8")


def test_compiler_demo_checks_survive_optimized_python() -> None:
    completed = _run_module("-O")
    assert completed.stderr == ""
    assert completed.stdout == EXPECTED.read_text(encoding="utf-8")


def test_compiler_demo_is_explicitly_snapshot_based() -> None:
    snapshots = build_demo_snapshots()
    assert {snapshot.source_type for snapshot in snapshots} == {
        "mcp",
        "openapi",
        "agent-skill",
        "framework",
        "a2a",
    }
    assert all(snapshot.metadata["discovery_executed"] is False for snapshot in snapshots)


def test_compiler_demo_surfaces_one_cross_source_identity_ambiguity() -> None:
    snapshots = build_demo_snapshots()
    capabilities = [
        capability for snapshot in snapshots for capability in snapshot.capabilities
    ]
    findings = analyze_collisions(capabilities).findings
    ambiguities = [
        finding for finding in findings if finding.kind in ("exact_name", "near_name")
    ]
    assert len(ambiguities) == 1
    assert ambiguities[0].item_ids == [
        "crm.draft_payment_reminder",
        EXPECTED_CAPABILITY_ID,
    ]
    assert {capability.namespace for capability in capabilities} >= {
        "crm",
        PHASE_NAMESPACE,
    }


def test_compiler_demo_degraded_fixture_removes_required_resource_digest() -> None:
    snapshots = build_demo_snapshots(degraded_skill_resource=True)
    skill_snapshot = next(
        snapshot for snapshot in snapshots if snapshot.source_type == "agent-skill"
    )
    assert skill_snapshot.resources[0].digest == ""
