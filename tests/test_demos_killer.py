"""Test the `killer` CLI scenario — the 60-second failure mode (issue #322).

The scenario contrasts a naive agent loop (100 tool descriptions + a long
history + a huge raw tool result, all inlined) against contextweaver
(ChoiceCard shortlist + firewalled result + compiled prompt). All asserted
numbers are **character**-based and therefore deterministic across
environments; the closing token estimate depends on the active tokeniser
and is not asserted.
"""

from __future__ import annotations

import io
import re
from contextlib import redirect_stdout

from contextweaver import _demos
from contextweaver.__main__ import _DemoScenario


def _run() -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        _demos.run_killer()
    return buf.getvalue()


def test_scenario_enum_includes_killer() -> None:
    """The CLI scenario flag must accept the new value."""
    assert _DemoScenario.killer.value == "killer"


def test_dispatch_wires_killer_handler() -> None:
    """Every enum value needs a handler; ``killer`` maps to ``run_killer``."""
    assert callable(_demos.run_killer)


def test_banner_and_footer_present() -> None:
    out = _run()
    assert "killer scenario (100 tools + huge output)" in out
    assert "Demo complete." in out


def test_catalog_is_one_hundred_tools() -> None:
    out = _run()
    assert "Catalog: 100 tools across 8 namespaces" in out


def test_tool_prompt_shrinks_to_a_five_card_shortlist() -> None:
    """100 raw tool descriptions (6,326 chars) collapse to 5 ChoiceCards (491 chars)."""
    out = _run()
    assert "naive (all 100 tools):        6,326 chars" in out
    assert "contextweaver (5 ChoiceCards):    491 chars" in out
    assert "reduction: 92.2%" in out
    assert "billing.invoices.search" in out


def test_huge_result_is_firewalled() -> None:
    """The ~14 KB invoice dump is firewalled to a 60-char summary (99.6% reduction)."""
    out = _run()
    assert "naive (raw):             14,430 chars" in out
    assert "contextweaver (summary):  60 chars" in out
    assert "reduction: 99.6%" in out
    assert "artifact artifact:result:tc1" in out


def test_full_answer_prompt_reduction() -> None:
    """The whole naive answer prompt (21,332 chars) compiles down to 814 chars."""
    out = _run()
    assert "naive (everything raw):   21,332 chars" in out
    assert "contextweaver (compiled):  814 chars" in out
    assert "reduction: 96.2%" in out


def test_token_estimate_line_present() -> None:
    """A token estimate is shown (value not asserted — tokeniser-dependent)."""
    out = _run()
    assert re.search(r"Token estimate: naive ~[\d,]+ tokens -> contextweaver ~[\d,]+ tokens", out)


def test_pct_reduction_helper() -> None:
    assert _demos._pct_reduction("x" * 1000, "x" * 250) == "75.0%"
    assert _demos._pct_reduction("x" * 100, "x" * 100) == "0.0%"


def test_big_result_exceeds_firewall_threshold() -> None:
    """The canned result must be large enough to actually trip the firewall."""
    assert len(_demos._killer_big_result()) > 2000
