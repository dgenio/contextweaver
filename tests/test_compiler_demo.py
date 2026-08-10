"""Regression tests for the package-native compiler-first adoption proof."""

from pathlib import Path

from contextweaver.compiler.__main__ import build_demo_snapshots, receipt_lines

EXPECTED = Path(__file__).parent / "fixtures" / "compiler_demo_expected.txt"


def test_compiler_demo_receipt_is_stable() -> None:
    actual = "\n".join(receipt_lines()) + "\n"
    assert actual == EXPECTED.read_text(encoding="utf-8")


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


def test_compiler_demo_degraded_fixture_removes_required_resource_digest() -> None:
    snapshots = build_demo_snapshots(degraded_skill_resource=True)
    skill_snapshot = next(snapshot for snapshot in snapshots if snapshot.source_type == "agent-skill")
    assert skill_snapshot.resources[0].digest == ""
