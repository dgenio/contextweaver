"""Smoke test for the live-transport MCP Context Gateway variant (issue #260).

Runs ``examples/architectures/mcp_context_gateway/main_live.py`` and pins
the deterministic invariants of the ProxyRuntime-driven gateway flow:
``tool_browse`` returns a bounded shortlist, schema hydration is lazy,
the firewall persists an artifact, and the rowset sentinel never appears
in the surfaced summary.
"""

from __future__ import annotations

import importlib.util
import io
import re
from contextlib import redirect_stdout
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MAIN_PATH = _REPO_ROOT / "examples" / "architectures" / "mcp_context_gateway" / "main_live.py"


def _load_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("mcp_context_gateway_main_live", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_main_live = _load_module(_MAIN_PATH)


@pytest.fixture
def live_run_output() -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        _main_live.main()
    return buf.getvalue()


def test_live_variant_loads_packaged_catalog(live_run_output: str) -> None:
    assert "Loaded catalog: 60 tools" in live_run_output


def test_live_variant_shortlist_is_bounded(live_run_output: str) -> None:
    match = re.search(r"shortlist \((\d+) of 60\):", live_run_output)
    assert match is not None
    assert int(match.group(1)) == 5


def test_live_variant_schema_only_hydrated_for_selected_tool(
    live_run_output: str,
) -> None:
    # ProxyRuntime mangles the tool id with a hash; we just check the
    # "0 chars (skipped)" line for the other 59 entries, which is the
    # load-bearing claim.
    assert "hydrated schema for the other 59 tools: 0 chars (skipped)" in live_run_output


def test_live_variant_firewall_runs_through_real_runtime(
    live_run_output: str,
) -> None:
    # Raw rowset matches main.py's 16,507-char fixture exactly.
    assert "raw upstream result: 16,507 chars" in live_run_output
    match = re.search(r"injected_summary_chars\s*=\s*(\d+)", live_run_output)
    assert match is not None
    summary_chars = int(match.group(1))
    # ProxyRuntime's firewall caps text summaries at 500 chars; pin both
    # the order of magnitude and the upper bound so a regression that
    # bypasses the firewall (e.g. summary == raw_result_chars) trips.
    assert 0 < summary_chars <= 1000


def test_live_variant_persists_artifact_handle(live_run_output: str) -> None:
    """A real artifact handle (not ``<none>``) means tool_view is wired up."""
    match = re.search(r"artifact_handle\s*=\s*(\S+)", live_run_output)
    assert match is not None
    handle = match.group(1)
    assert handle.startswith("text:"), f"unexpected handle prefix: {handle!r}"


def test_live_variant_rowset_does_not_leak_into_summary(
    live_run_output: str,
) -> None:
    """The sentinel that only exists in deep rowset content must not appear
    anywhere outside the raw-result narration line (the canonical firewall
    invariant)."""
    sentinel = '"mrr_delta_usd": -450'
    # The Metrics summary block is the post-firewall surface; sentinel must
    # not be there.
    metrics_section = live_run_output.split("Metrics summary (live)", 1)[1]
    assert sentinel not in metrics_section


def test_live_variant_tool_view_drilldown_returns_head_slice(
    live_run_output: str,
) -> None:
    """``tool_view`` exercised through the dispatcher returns the header lines."""
    assert "tool_view returned" in live_run_output
    assert "rowset: bigquery.run_query" in live_run_output
