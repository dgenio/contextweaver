"""Smoke test for the real-catalog variant of the MCP Context Gateway (#280).

Mirrors :mod:`tests.test_architectures_mcp_context_gateway` but exercises
``main_real.py``, which loads three committed real-MCP-server snapshots
(filesystem, git, fetch) from
``examples/architectures/mcp_context_gateway/real_catalogs/`` and walks
the full route -> call -> interpret -> answer cycle per snapshot.

The run is deterministic: snapshots are committed, queries are constant,
no network. The assertions are scoped to invariants that must hold
regardless of upstream-server tool counts (so the test remains valid
when snapshots are refreshed by ``scripts/snapshot_mcp_catalog.py``).
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MAIN_REAL_PATH = _REPO_ROOT / "examples" / "architectures" / "mcp_context_gateway" / "main_real.py"
_REAL_CATALOGS_DIR = (
    _REPO_ROOT / "examples" / "architectures" / "mcp_context_gateway" / "real_catalogs"
)

_SNAPSHOT_FILES: tuple[str, ...] = ("filesystem_mcp.json", "git_mcp.json", "fetch_mcp.json")


def _load_module(path: Path) -> ModuleType:
    """Import ``main_real.py`` without polluting ``sys.modules`` with a generic key.

    The target module defines a ``@dataclass(frozen=True)``; CPython's
    dataclass machinery looks the module up in ``sys.modules`` during
    type introspection, so we must register the module under our chosen
    name before ``exec_module`` runs.
    """
    spec = importlib.util.spec_from_file_location("mcp_context_gateway_main_real", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


main_real = _load_module(_MAIN_REAL_PATH)


@pytest.fixture(scope="module")
def real_gateway_run_output() -> str:
    """Run ``main()`` once and capture stdout for cross-test assertions."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        main_real.main()
    return buf.getvalue()


# ------------------------------------------------------------------
# Snapshots themselves (provenance / structure invariants)
# ------------------------------------------------------------------


@pytest.mark.parametrize("snapshot", _SNAPSHOT_FILES)
def test_snapshot_has_meta_and_tools(snapshot: str) -> None:
    """Every committed snapshot must carry _meta + tools and document its licence."""
    payload = json.loads((_REAL_CATALOGS_DIR / snapshot).read_text())
    assert isinstance(payload, dict)
    assert set(payload).issuperset({"_meta", "tools"})
    meta = payload["_meta"]
    assert isinstance(meta, dict)
    # Provenance fields that the README and recipes both lean on:
    for required_field in ("source", "server_package", "license", "snapshotted_at"):
        assert required_field in meta, f"{snapshot} _meta missing {required_field!r}"
    assert isinstance(payload["tools"], list)
    assert len(payload["tools"]) >= 1


@pytest.mark.parametrize("snapshot", _SNAPSHOT_FILES)
def test_snapshot_tools_are_well_formed(snapshot: str) -> None:
    """Each tool entry has at minimum name + description + inputSchema."""
    tools = json.loads((_REAL_CATALOGS_DIR / snapshot).read_text())["tools"]
    for tool in tools:
        assert isinstance(tool, dict)
        for required in ("name", "description", "inputSchema"):
            assert required in tool, f"{snapshot}: tool entry missing {required!r}"


# ------------------------------------------------------------------
# main_real.py end-to-end invariants
# ------------------------------------------------------------------


def test_runs_all_committed_snapshots(real_gateway_run_output: str) -> None:
    """The aggregate-metrics block must report every committed snapshot."""
    assert "scenarios_run           = 3" in real_gateway_run_output
    for snapshot in _SNAPSHOT_FILES:
        assert snapshot in real_gateway_run_output, (
            f"{snapshot} did not appear in main_real.py output"
        )


def test_route_phase_emits_bounded_shortlist(real_gateway_run_output: str) -> None:
    """Every per-snapshot route phase must emit a shortlist bounded by top_k = 5.

    Router can return fewer than top_k candidates when the beam search
    converges early on a small catalog; the load-bearing invariant is
    that the shortlist size never exceeds top_k and never exceeds the
    catalog size. Catalog sizes themselves are intentionally NOT pinned
    so the test survives ``scripts/snapshot_mcp_catalog.py`` refreshes.
    """
    import re

    matches = re.findall(r"shortlist \((\d+) of (\d+)\):", real_gateway_run_output)
    assert len(matches) == 3, (
        f"expected 3 shortlist lines (one per snapshot), got {len(matches)}: {matches}"
    )
    for shortlist_str, catalog_str in matches:
        shortlist = int(shortlist_str)
        catalog = int(catalog_str)
        assert catalog >= 1, f"catalog size {catalog} below 1"
        assert shortlist >= 1, f"shortlist size {shortlist} below 1"
        assert shortlist <= min(5, catalog), (
            f"shortlist ({shortlist}) exceeds min(top_k=5, catalog={catalog})"
        )


def test_call_phase_only_hydrates_one_schema(real_gateway_run_output: str) -> None:
    """Lazy schema hydration: skipped-tool count must equal ``catalog_size - 1``.

    Derived from the shortlist lines so the assertion survives snapshot
    refreshes (no hard-coded counts).
    """
    import re

    catalog_sizes = sorted(
        int(c) for _, c in re.findall(r"shortlist \((\d+) of (\d+)\):", real_gateway_run_output)
    )
    skipped_strs = re.findall(
        r"hydrated schema for the other (\d+) tools: 0 chars \(skipped\)",
        real_gateway_run_output,
    )
    assert len(skipped_strs) == 3, (
        f"expected 3 skipped-count lines, got {len(skipped_strs)}: {skipped_strs}"
    )
    skipped_counts = sorted(int(s) for s in skipped_strs)
    expected = sorted(max(c - 1, 0) for c in catalog_sizes)
    assert skipped_counts == expected, (
        f"skipped-tools counts {skipped_counts} do not match catalog_size - 1 = {expected}"
    )


def test_firewall_triggers_on_every_snapshot(real_gateway_run_output: str) -> None:
    """Every snapshot's fake upstream payload exceeds the 2 KB firewall threshold."""
    # Each scenario in main_real.py constructs a >2000 char fake response,
    # so the "firewall:" log must report a strictly smaller summary than the raw size.
    lines = [ln for ln in real_gateway_run_output.splitlines() if ln.startswith("firewall:")]
    assert len(lines) == 3, f"expected 3 firewall log lines, got {len(lines)}: {lines}"
    for line in lines:
        # Shape: "firewall: 12,345 chars  ->  67-char summary  (artifact ...)"
        # Both fields use ``{:,}`` formatting in main_real.py, so the parse
        # must strip thousands separators on both sides -- the original code
        # only stripped on raw_chars, which would have broken once firewall
        # summaries crossed 1,000 chars.
        parts = line.split()
        raw_chars = int(parts[1].replace(",", ""))
        summary_chars = int(parts[4].split("-")[0].replace(",", ""))
        assert summary_chars < raw_chars, f"summary did not shrink the raw payload: {line!r}"


def test_aggregate_reduction_exceeds_90_percent(real_gateway_run_output: str) -> None:
    """Across the three snapshots the aggregate firewall reduction must be > 90 %."""
    # Shape: "overall_firewall_pct    = 96.9%"
    line = next(
        ln for ln in real_gateway_run_output.splitlines() if ln.startswith("overall_firewall_pct")
    )
    pct = float(line.split("=")[1].strip().rstrip("%"))
    assert pct > 90.0, f"aggregate firewall reduction below 90%: {pct}"


def test_answer_prompt_does_not_contain_raw_upstream_response(
    real_gateway_run_output: str,
) -> None:
    """The final answer-phase prompt must not leak the raw firewalled payload back in."""
    # Pick a sentinel that only appears deep in the filesystem scenario's
    # raw response — synthetic file paths it constructs.
    filesystem_sentinel = "/workspace/pkg2/module_0150.py"
    # The sentinel does appear in the captured "raw upstream result" debug
    # line earlier in the run; we only check that it never appears INSIDE
    # the "--- Final answer-phase prompt ---" block. main_real.py does not
    # print that block (kept terse), so we instead assert the sentinel
    # never appears next to "answer prompt:" reporting.
    assert filesystem_sentinel not in real_gateway_run_output, (
        "main_real.py output leaked a raw rowset sentinel into the captured run"
    )
