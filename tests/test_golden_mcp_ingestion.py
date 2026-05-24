"""Golden fixtures for ``mcp_result_to_envelope`` ingestion (issue #296).

Drives a small set of representative MCP tool-call results through the
adapter and compares the produced :class:`ResultEnvelope` against
checked-in JSON fixtures.  Volatile fields are normalised by
``tests.fixtures._normalize``.

See ``docs/contributing_fixtures.md`` for the regeneration policy.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from contextweaver.adapters.mcp import mcp_result_to_envelope
from tests.fixtures._normalize import load_fixture, normalize, to_canonical_json

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "golden" / "mcp_ingestion"

_FIXTURES = sorted(FIXTURE_DIR.glob("*.json"))


@pytest.mark.parametrize("fixture_path", _FIXTURES, ids=lambda p: p.stem)
def test_mcp_ingestion_matches_golden(fixture_path: Path) -> None:
    """Each fixture's ``input`` produces its checked-in ``expected``.

    Failure messages include the fixture file path so the diff is
    actionable.
    """
    fixture = load_fixture(fixture_path)
    env, binaries, full_text = mcp_result_to_envelope(fixture["input"], fixture["tool_name"])

    actual = normalize(
        {
            "envelope": env.to_dict(),
            "full_text": full_text,
            "binary_handles": sorted(binaries.keys()),
            "binary_sizes": {h: len(blob[0]) for h, blob in binaries.items()},
        }
    )
    # Reduce expected to only the keys that are present in *actual* —
    # the text/error fixtures omit ``binary_sizes`` because no binaries
    # were emitted.
    expected = normalize(fixture["expected"])
    if "binary_sizes" not in expected:
        actual.pop("binary_sizes", None)

    if actual != expected:
        # Build a readable diff for the file-bearing AssertionError.
        diff = (
            f"\n--- expected ({fixture_path}):\n"
            + to_canonical_json(expected)
            + f"\n--- actual ({fixture_path}):\n"
            + to_canonical_json(actual)
        )
        raise AssertionError(f"MCP-ingestion fixture drifted: {fixture_path}\n{diff}")


def test_mcp_ingestion_fixture_set_is_non_empty() -> None:
    """Defends against accidental deletion of the fixture directory."""
    assert _FIXTURES, f"no golden fixtures under {FIXTURE_DIR}"


def test_mcp_ingestion_fixture_covers_text_image_error() -> None:
    """Pin the scenario coverage so we catch regressions in fixture
    breadth, not just per-fixture drift."""
    stems = {p.stem for p in _FIXTURES}
    required = {"text_result", "image_result", "error_result"}
    missing = required - stems
    assert not missing, f"missing golden MCP-ingestion scenarios: {sorted(missing)}"
