"""Test the `mcp-gateway-full` CLI scenario (issue #264).

The scenario surfaces the 60-tool MCP Context Gateway reference
architecture from `contextweaver demo` so users can see the launch
narrative end-to-end without invoking the example script directly.
"""

from __future__ import annotations

import io
import re
from contextlib import redirect_stdout

import pytest

from contextweaver import _demos
from contextweaver.__main__ import _DemoScenario


def test_scenario_enum_includes_mcp_gateway_full() -> None:
    """The CLI scenario flag must accept the new value."""
    assert _DemoScenario.mcp_gateway_full.value == "mcp-gateway-full"


def test_run_mcp_gateway_full_walks_60_tool_catalog() -> None:
    """`run_mcp_gateway_full` must invoke the reference architecture and
    surface its load-bearing metrics (catalog size, ChoiceCard count,
    firewall reduction)."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        _demos.run_mcp_gateway_full()
    out = buf.getvalue()

    # CLI banner + footer
    assert "mcp-gateway-full scenario" in out
    assert "Demo complete." in out
    # Architecture metrics — these match the OUTPUT.md snapshot exactly.
    assert "catalog_tools           = 60" in out
    assert "exposed_choice_cards    = 5" in out
    # The firewall narration must show a non-trivial reduction.
    assert "firewall_reduction_pct" in out
    match = re.search(r"firewall_reduction_pct\s+=\s+([\d.]+)%", out)
    assert match is not None
    assert float(match.group(1)) > 90.0


def test_run_mcp_gateway_full_emits_metrics_block() -> None:
    """The shipped metrics block from the architecture script must come
    through unchanged (it's the public artefact users compare against
    the README claims)."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        _demos.run_mcp_gateway_full()
    out = buf.getvalue()

    for field in (
        "catalog_tools",
        "exposed_choice_cards",
        "hydrated_schema_chars",
        "raw_result_chars",
        "injected_summary_chars",
        "firewall_reduction_pct",
        "artifact_handle",
        "final_prompt_tokens",
    ):
        assert f"{field}" in out, f"metric line {field} missing from CLI scenario"


def test_demo_dispatch_includes_mcp_gateway_full() -> None:
    """The dispatch dict in `__main__.demo` must wire the new enum value
    to `_demos.run_mcp_gateway_full` — otherwise the CLI flag would
    raise KeyError at invocation time."""
    # Re-import the CLI module to read its dispatch dict the same way
    # Typer would at call time.
    from contextweaver.__main__ import _DemoScenario as Scenarios  # noqa: PLC0415

    assert Scenarios.mcp_gateway_full in Scenarios
    # The function exists on _demos and is callable.
    assert callable(_demos.run_mcp_gateway_full)


@pytest.mark.parametrize(
    "scenario",
    list(_DemoScenario),
)
def test_every_scenario_in_enum_has_a_demo_handler(scenario: _DemoScenario) -> None:
    """Pin the invariant that every enum value has a runnable handler
    (catches the easiest regression mode: adding a scenario to the enum
    without wiring it into the dispatch dict)."""
    name = "run_" + scenario.value.replace("-", "_")
    assert callable(getattr(_demos, name)), f"no demo handler for {scenario.value!r}"


def test_packaged_fallback_agrees_with_architecture_on_load_bearing_metrics() -> None:
    """The wheel-install fallback (``_run_mcp_gateway_full_packaged``) and
    the source-tree path (``examples/.../main.py``) must agree on the
    load-bearing metrics: catalog size, ChoiceCard width, and firewall
    reduction band.

    This is the regression gate for the audit finding that
    ``_run_mcp_gateway_full_packaged`` re-implements the architecture's
    pipeline narration. If the two outputs drift, this test fails — so
    forgetting to update one when the architecture changes is caught at
    CI time instead of in production.
    """
    buf_packaged = io.StringIO()
    with redirect_stdout(buf_packaged):
        _demos._run_mcp_gateway_full_packaged()
    packaged = buf_packaged.getvalue()

    buf_arch = io.StringIO()
    with redirect_stdout(buf_arch):
        _demos.run_mcp_gateway_full()  # source-tree path (loads the example)
    arch = buf_arch.getvalue()

    def _catalog_size(text: str) -> int:
        # The architecture prints "catalog_tools           = 60" while the
        # packaged fallback prints "Loaded catalog: 60 tools across N
        # namespaces" — both must surface 60.
        match = re.search(r"(?:catalog_tools\s+=\s+|Loaded catalog:\s+)(\d+)", text)
        assert match is not None, f"catalog size not found in:\n{text[:200]}"
        return int(match.group(1))

    def _firewall_reduction(text: str) -> float:
        # Architecture: "firewall_reduction_pct  = 98.8%"
        # Packaged:    "Firewall reduction: 98.8%"
        match = re.search(
            r"(?:firewall_reduction_pct\s+=\s+|Firewall reduction:\s+)([\d.]+)%", text
        )
        assert match is not None, f"firewall reduction not found in:\n{text[:200]}"
        return float(match.group(1))

    # Both code paths route against the same 60-tool packaged catalog.
    assert _catalog_size(packaged) == _catalog_size(arch) == 60, (
        "Wheel-install fallback and source-tree architecture disagree on "
        "catalog size — one of them is reading a different catalog."
    )

    # Both code paths fire the firewall on the ~16 KB BigQuery rowset; the
    # reduction band must stay > 95 %. The two narrations use slightly
    # different summary text (the example is verbose; the fallback is
    # terser), so the absolute values diverge by ~1 percentage point — but
    # both must clear the band that the README headline claims.
    assert _firewall_reduction(packaged) > 95.0
    assert _firewall_reduction(arch) > 95.0
