"""Smoke test for the MCP Context Gateway reference architecture.

The architecture is exercised by ``make example`` via the ``architectures``
umbrella target. This unit test pins the deterministic invariants of the
scripted run so regressions in routing, schema hydration, or the firewall
surface immediately rather than after a CI ``make example`` failure.

The script is deterministic — fixed seed, no randomness, no network, no
LLM, no real MCP server — so we can assert specific counts and substrings
rather than just "ran without exception".
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
_MAIN_PATH = _REPO_ROOT / "examples" / "architectures" / "mcp_context_gateway" / "main.py"


def _load_module(path: Path) -> ModuleType:
    """Import the architecture's ``main.py`` without polluting ``sys.modules``
    with a generic ``main`` key — `tests/test_architectures_slack.py` also has
    a ``main.py`` and the two would otherwise collide when both tests run in
    the same pytest session."""
    spec = importlib.util.spec_from_file_location("mcp_context_gateway_main", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mcp_context_gateway = _load_module(_MAIN_PATH)


@pytest.fixture
def gateway_run_output() -> str:
    """Run ``main()`` once and capture stdout for assertions."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        mcp_context_gateway.main()
    return buf.getvalue()


# ------------------------------------------------------------------
# Catalog
# ------------------------------------------------------------------


def test_catalog_loads_with_expected_size(gateway_run_output: str) -> None:
    """catalog.yaml ships exactly 60 tools across 10 namespaces."""
    assert "Loaded catalog: 60 tools across 10 namespaces" in gateway_run_output


def test_catalog_tools_metric_exposed(gateway_run_output: str) -> None:
    assert "catalog_tools           = 60" in gateway_run_output


# ------------------------------------------------------------------
# Route phase — bounded ChoiceCards
# ------------------------------------------------------------------


def test_only_a_bounded_number_of_cards_are_exposed(gateway_run_output: str) -> None:
    """The route phase must shortlist to 5 cards out of 60 — the launch claim
    that the model never sees the full catalog at once."""
    assert "shortlist (5 of 60):" in gateway_run_output
    assert "exposed_choice_cards    = 5" in gateway_run_output


def test_rendered_choice_cards_are_compact(gateway_run_output: str) -> None:
    """ChoiceCards rendered to the model must be bounded in size and must NOT
    contain any of the inputSchema bytes (which would defeat the purpose)."""
    match = re.search(r"ChoiceCards rendered to the model \((\d+) chars", gateway_run_output)
    assert match is not None, "card-render header missing"
    rendered_chars = int(match.group(1))
    # 5 cards × ~100 chars each is the right order of magnitude; we cap at 2 KB
    # to catch a regression that would inline full schemas.
    assert rendered_chars < 2000, f"ChoiceCards rendered to {rendered_chars} chars — too large"
    # Spot-check: a JSON Schema marker has no business being in card text.
    cards_section = gateway_run_output.split("ChoiceCards rendered to the model", 1)[1]
    cards_section = cards_section.split("=" * 60, 1)[0]
    assert '"inputSchema"' not in cards_section
    assert '"properties"' not in cards_section


def test_selected_intent_is_routed_top_one(gateway_run_output: str) -> None:
    """The deterministic routing query was tuned so the intent ranks #1."""
    assert "chosen:    bigquery.run_query  (intent='bigquery.run_query')" in gateway_run_output


# ------------------------------------------------------------------
# Call phase — schema hydration is lazy
# ------------------------------------------------------------------


def test_only_the_selected_tool_schema_is_hydrated(gateway_run_output: str) -> None:
    """The other 59 tools must not have their schemas materialised at all."""
    assert "hydrated schema for: 'bigquery.run_query'" in gateway_run_output
    assert "hydrated schema for the other 59 tools: 0 chars" in gateway_run_output


# ------------------------------------------------------------------
# Firewall — large output is NOT injected into the final prompt
# ------------------------------------------------------------------


def test_firewall_collapses_large_result(gateway_run_output: str) -> None:
    """Raw 16 KB MCP result collapses to <500 chars on the prompt side."""
    # Pin order of magnitude on raw_result_chars; the mock generator could
    # change without breaking the invariant.
    match = re.search(r"raw_result_chars\s*=\s*([\d,]+)", gateway_run_output)
    assert match is not None
    raw_chars = int(match.group(1).replace(",", ""))
    assert raw_chars > 10_000, f"mock upstream result shrank to {raw_chars} — recheck mock"

    match = re.search(r"injected_summary_chars\s*=\s*([\d,]+)", gateway_run_output)
    assert match is not None
    summary_chars = int(match.group(1).replace(",", ""))
    assert summary_chars < 500, f"firewall summary grew to {summary_chars} — regression"


def test_firewall_persists_artifact(gateway_run_output: str) -> None:
    """The full raw bytes must land in the artifact store with a handle."""
    assert "artifact_handle         = artifact:" in gateway_run_output
    # The firewall narration line must explicitly mention the handle.
    assert re.search(
        r"firewall: [\d,]+ chars\s+->\s+\d+-char summary\s+\(artifact ", gateway_run_output
    )


def test_large_output_is_not_injected_raw_into_final_prompt(gateway_run_output: str) -> None:
    """The single most load-bearing invariant of this architecture: the deep
    rowset content must not appear in the final answer-phase prompt.

    Sentinel ``"mrr_delta_usd": -450`` only appears in day-47's row (the
    downgrade event) — far from any header line that might leak into the
    firewall summary. If this string lands in the prompt, the firewall has
    been bypassed."""
    sentinel = '"mrr_delta_usd": -450'
    final_section = gateway_run_output.split("Final answer-phase prompt", 1)[1]
    final_section = final_section.split("--- end prompt ---", 1)[0]
    assert sentinel not in final_section, (
        f"Raw rowset content leaked into final prompt — sentinel {sentinel!r} found"
    )
    # Also pin the explicit narration line:
    assert "contains raw rowset? no" in gateway_run_output


# ------------------------------------------------------------------
# Answer phase — summary + handle + dependency chain
# ------------------------------------------------------------------


def test_final_prompt_renders_summary_and_handle(gateway_run_output: str) -> None:
    """The final prompt must show the firewall summary AND the artifact ref
    so an agent can drill into the full bytes via tool_view."""
    final = gateway_run_output.split("Final answer-phase prompt", 1)[1]
    final = final.split("--- end prompt ---", 1)[0]
    assert "[TOOL RESULT" in final
    assert "artifact:" in final
    assert "rows_returned: 90" in final, "header summary missing"


def test_final_prompt_renders_dependency_chain(gateway_run_output: str) -> None:
    """The [TOOL CALL] section must be present — dependency closure means the
    tool call survives selection because its child (the tool result) was
    included."""
    final = gateway_run_output.split("Final answer-phase prompt", 1)[1]
    final = final.split("--- end prompt ---", 1)[0]
    assert "[USER]" in final
    assert "[TOOL CALL]" in final
    assert "[FACTS]" in final


def test_final_prompt_contains_durable_fact(gateway_run_output: str) -> None:
    """add_fact_sync persisted a fact that names the cause; it must survive
    into the answer-phase prompt."""
    assert "customer.C-12345.plan_change" in gateway_run_output
    assert "growth -> starter" in gateway_run_output


# ------------------------------------------------------------------
# Metrics block — every metric the README documents must be emitted
# ------------------------------------------------------------------


def test_metrics_block_emits_documented_fields(gateway_run_output: str) -> None:
    for field in (
        "catalog_tools",
        "exposed_choice_cards",
        "hydrated_schema_chars",
        "raw_result_chars",
        "injected_summary_chars",
        "firewall_reduction_pct",
        "artifact_handle",
        "final_prompt_tokens",
        "final_prompt_chars",
    ):
        assert re.search(rf"{re.escape(field)}\s*=", gateway_run_output), (
            f"metric {field!r} missing from output — README/OUTPUT.md is now out of sync"
        )


def test_select_from_shortlist_prefers_intent_when_present() -> None:
    """Intent-map helper picks the intent if it is in the shortlist."""
    assert mcp_context_gateway._select_from_shortlist(["a.x", "b.y", "c.z"], "b.y") == "b.y"


def test_select_from_shortlist_falls_back_to_top_when_missing() -> None:
    """Helper falls back to the top-1 candidate when the intent is absent."""
    assert mcp_context_gateway._select_from_shortlist(["a.x", "b.y", "c.z"], "absent") == "a.x"
