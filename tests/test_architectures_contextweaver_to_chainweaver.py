"""Smoke test for the contextweaver -> ChainWeaver reference architecture (#353).

The architecture is exercised by ``make example`` via the ``architectures``
umbrella target. This unit test pins the deterministic invariants of the
route -> execute -> ingest seam so regressions in flow import (#334), routing
to a ``kind="flow"`` candidate, the firewall, or the weaver-spec contract
mapping (#320) surface immediately.

The run is deterministic and offline (the ChainWeaver runtime is stubbed), so
we assert specific outcomes rather than just "ran without exception".
"""

from __future__ import annotations

import importlib.util
import io
from contextlib import redirect_stdout
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MAIN_PATH = _REPO_ROOT / "examples" / "architectures" / "contextweaver_to_chainweaver" / "main.py"
_spec = importlib.util.spec_from_file_location("cw_to_chainweaver_main", _MAIN_PATH)
assert _spec is not None and _spec.loader is not None
cw_to_chainweaver = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cw_to_chainweaver)


@pytest.fixture
def run_output() -> str:
    """Run ``main()`` once and capture stdout for assertions."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        cw_to_chainweaver.main()
    return buf.getvalue()


def test_catalog_mixes_tools_and_flows(run_output: str) -> None:
    assert "Loaded catalog: 5 items (2 ChainWeaver flows + 3 tools)" in run_output


def test_routes_to_a_flow(run_output: str) -> None:
    """The customer-history query must route to the ChainWeaver flow, not a tool."""
    assert "selected:  chainweaver:customer_summary_flow  (kind='flow')" in run_output
    assert "routed to a ChainWeaver flow: True" in run_output


def test_advisory_candidate_is_a_flow(run_output: str) -> None:
    assert '"candidate_type": "flow"' in run_output
    assert '"advisory": true' in run_output
    assert '"runtime_flow_id": "customer_summary_flow"' in run_output


def test_weaver_spec_contract_mapping_runs(run_output: str) -> None:
    """The [weaver-spec] extra is in the dev env, so the mapping must succeed."""
    assert "weaver-spec RoutingDecision id: rd-" in run_output
    assert "weaver-spec Frame id: frame-cust-42" in run_output
    assert "weaver-spec mapped:    decision=True frame=True" in run_output


def test_firewall_fires_on_flow_result(run_output: str) -> None:
    assert "firewall: 4,002 chars ->" in run_output
    assert "char summary" in run_output
    assert "firewall fired:        True" in run_output
    assert "artifacts kept:        1" in run_output


def test_stub_runtime_is_deterministic() -> None:
    runtime = cw_to_chainweaver._StubChainWeaverRuntime()
    first = runtime.execute("customer_summary_flow", {"customer_id": "cust-42"})
    second = runtime.execute("customer_summary_flow", {"customer_id": "cust-42"})
    assert first == second
    assert "cust-42" in first
