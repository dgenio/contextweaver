"""Smoke test for the multi-turn MCP Context Gateway variant (issue #262).

Pins the three invariants the issue explicitly calls out:

1. Every turn renders a ``Turn N`` banner.
2. Facts persisted in turn k appear in the prompts of turns > k.
3. Cumulative firewall reduction stays above 95 %.
"""

from __future__ import annotations

import importlib.util
import io
import re
import sys
from contextlib import redirect_stdout
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MAIN_PATH = _REPO_ROOT / "examples" / "architectures" / "mcp_context_gateway" / "main_multi.py"


def _load_module(path: Path) -> ModuleType:
    module_name = "mcp_context_gateway_main_multi"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Insert into ``sys.modules`` before exec so frozen dataclasses can
    # resolve type annotations against the module's own namespace.
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_main_multi = _load_module(_MAIN_PATH)


@pytest.fixture
def multi_run_output() -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        _main_multi.main()
    return buf.getvalue()


# ------------------------------------------------------------------
# Turn banners — every turn must emit a Turn N header.
# ------------------------------------------------------------------


def test_all_five_turns_emit_a_turn_banner(multi_run_output: str) -> None:
    for n in range(1, 6):
        assert f"Turn {n} —" in multi_run_output, f"Turn {n} banner missing"


# ------------------------------------------------------------------
# Cross-turn fact accumulation
# ------------------------------------------------------------------


def test_persisted_facts_include_all_four_keys(multi_run_output: str) -> None:
    """Each fact_key supplied in the transcript must persist into the store."""
    expected_keys = (
        "customer.C-12345.plan_change",
        "customer.C-12345.contact_owner",
        "incident.C-12345.tracking_ticket",
        "incident.C-12345.broadcast",
    )
    for key in expected_keys:
        assert key in multi_run_output, f"fact key {key!r} missing from output"


def test_persisted_facts_count_matches_transcript(multi_run_output: str) -> None:
    """Four turns supply facts (Turn 3 deliberately doesn't); count must be 4."""
    match = re.search(r"persisted_facts_count\s*=\s*(\d+)", multi_run_output)
    assert match is not None
    assert int(match.group(1)) == 4


# ------------------------------------------------------------------
# Cumulative firewall reduction
# ------------------------------------------------------------------


def test_cumulative_firewall_reduction_above_95_percent(multi_run_output: str) -> None:
    """The aggregate firewall reduction across 5 turns must stay > 95 %."""
    match = re.search(r"cumulative_firewall_pct\s*=\s*([\d.]+)%", multi_run_output)
    assert match is not None
    cumulative = float(match.group(1))
    assert cumulative > 95.0, f"cumulative firewall reduction dropped to {cumulative}% — regression"


def test_raw_upstream_chars_total_is_realistic(multi_run_output: str) -> None:
    """Combined raw upstream chars across all 5 turns must exceed 30 KB
    (Turn 1 alone is ~16 KB, Turn 5 alone is ~20 KB)."""
    match = re.search(r"raw_upstream_chars_total\s*=\s*([\d,]+)", multi_run_output)
    assert match is not None
    total = int(match.group(1).replace(",", ""))
    assert total > 30_000, f"raw upstream total shrank to {total} — re-check mock data"


# ------------------------------------------------------------------
# Turn 1 rowset never leaks into later turns' prompts
# ------------------------------------------------------------------


def test_turn1_rowset_does_not_leak_into_any_later_turn(
    multi_run_output: str,
) -> None:
    """For every turn that prints ``contains raw Turn-1 rowset?``, the answer
    must be ``no``. This is the load-bearing cross-turn firewall guarantee."""
    matches = re.findall(
        r"contains raw Turn-1 rowset\? (yes|no|YES \(regression!\))",
        multi_run_output,
    )
    assert matches, "no rowset-leak narration lines found"
    assert all(answer == "no" for answer in matches), (
        f"rowset leaked into a later turn's prompt — {matches}"
    )


# ------------------------------------------------------------------
# Token growth — context accumulates monotonically
# ------------------------------------------------------------------


def test_per_turn_prompt_tokens_increase_monotonically(
    multi_run_output: str,
) -> None:
    """Each subsequent turn carries more accumulated context — final-prompt
    tokens must be strictly non-decreasing across the five turns."""
    match = re.search(r"per_turn_prompt_tokens\s*=\s*\[([\d,\s]+)\]", multi_run_output)
    assert match is not None
    tokens = [int(t.strip()) for t in match.group(1).split(",")]
    assert len(tokens) == 5
    for i in range(1, len(tokens)):
        assert tokens[i] >= tokens[i - 1], f"prompt tokens regressed at turn {i + 1}: {tokens}"


# ------------------------------------------------------------------
# Routing stability — bigquery.run_query is still top-1 in Turn 1
# ------------------------------------------------------------------


def test_turn1_routes_to_bigquery_run_query(multi_run_output: str) -> None:
    turn1_section = multi_run_output.split("Turn 1 —", 1)[1].split("Turn 2 —", 1)[0]
    assert "bigquery.run_query" in turn1_section
    assert "chosen:    bigquery.run_query  (intent='bigquery.run_query')" in turn1_section
