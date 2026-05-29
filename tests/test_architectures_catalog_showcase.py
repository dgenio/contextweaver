"""Smoke test for the catalog showcase reference architecture (#330).

The showcase is exercised by ``make example`` via the ``architectures``
umbrella target. This unit test pins the deterministic invariants of the
adoption story — catalog size, shortlist contents, single-tool hydration,
firewall reduction, artifact persistence — so regressions surface here
rather than in a CI ``make example`` failure.

Token counts are intentionally **not** asserted: the tokeniser falls back
to a chars/4 estimate when the tiktoken encoding cannot be downloaded, so
per-section token numbers differ between environments. Everything asserted
below is character- or count-based and therefore environment-independent.
"""

from __future__ import annotations

import importlib.util
import io
from contextlib import redirect_stdout
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MAIN_PATH = _REPO_ROOT / "examples" / "architectures" / "catalog_showcase" / "main.py"
_spec = importlib.util.spec_from_file_location("catalog_showcase_main", _MAIN_PATH)
assert _spec is not None and _spec.loader is not None
catalog_showcase = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(catalog_showcase)


@pytest.fixture
def showcase_output() -> str:
    """Run ``main()`` once and capture stdout for assertions."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        catalog_showcase.main()
    return buf.getvalue()


def test_catalog_loads_at_scale(showcase_output: str) -> None:
    """The synthetic catalog ships 65 tools across 9 namespaces."""
    assert "Loaded catalog: 65 tools across 9 namespaces" in showcase_output


def test_request_routes_to_a_five_tool_shortlist(showcase_output: str) -> None:
    """The route phase narrows 65 tools to a 5-card shortlist."""
    assert "shortlist: 5 of 65 tools" in showcase_output


def test_intent_tool_tops_the_shortlist(showcase_output: str) -> None:
    """``commerce.product_search`` is the intended tool and lands in the shortlist."""
    assert (
        "chosen:   commerce.product_search  (intent='commerce.product_search', in shortlist)"
        in (showcase_output)
    )
    assert "NOT in shortlist" not in showcase_output


def test_only_the_selected_tool_is_hydrated(showcase_output: str) -> None:
    """Exactly one tool's schema is hydrated; the other 64 cost zero schema bytes."""
    assert "hydrated schema for 'commerce.product_search': 653 chars" in showcase_output
    assert "hydrated schema for the other 64 tools: 0 chars (never paid for)" in showcase_output


def test_firewall_compacts_the_large_result(showcase_output: str) -> None:
    """The ~3 KB search payload is firewalled to a 501-char summary (84% reduction)."""
    assert "firewall: 3,128 chars -> 501-char summary" in showcase_output
    assert "prompt-side reduction: 84.0%" in showcase_output
    assert "artifact artifact:result:tc1" in showcase_output


def test_raw_bytes_stay_addressable(showcase_output: str) -> None:
    """Every tool result is persisted in the artifact store for drilldown."""
    assert "artifacts kept (addressable for drilldown): 1" in showcase_output


def test_final_prompt_has_expected_sections(showcase_output: str) -> None:
    """The answer-phase prompt renders the user turn, the tool result, and the call."""
    final_section = showcase_output.split("Final answer-phase prompt", 1)[1]
    assert "[USER]" in final_section
    assert "[TOOL RESULT" in final_section
    assert "[TOOL CALL]" in final_section


def test_buildstats_includes_three_items(showcase_output: str) -> None:
    """The answer build keeps the user turn, the firewalled result, and the call."""
    assert "included_count:      3" in showcase_output


def test_select_from_shortlist_prefers_intent_when_present() -> None:
    """The intent-map helper picks the intent if it is in the shortlist."""
    assert catalog_showcase._select_from_shortlist(["a.x", "b.y", "c.z"], "b.y") == "b.y"


def test_select_from_shortlist_falls_back_to_top_when_missing() -> None:
    """The helper falls back to the top-1 candidate when the intent is absent."""
    assert catalog_showcase._select_from_shortlist(["a.x", "b.y", "c.z"], "absent") == "a.x"
