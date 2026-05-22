"""Smoke test for the MCP Context Gateway multi-turn variant (#262).

Pins the load-bearing invariants of the 4-turn transcript:

- All four turns route, hydrate, ingest, and build an answer prompt.
- Every turn's intent appears in the shortlist (4/4) — proves the catalog
  yaml + routing config can serve a realistic conversational shape.
- Facts accumulate monotonically (1 → 2 → 3 → 4).
- Per-turn answer-token counts stay bounded (no exponential blow-up).
- Turn 1's BigQuery artifact survives into the final answer prompt
  (dependency closure works across turns — this is the load-bearing
  multi-turn claim).
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
_MAIN_PATH = _REPO_ROOT / "examples" / "architectures" / "mcp_context_gateway" / "main_multi.py"


def _load_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("mcp_context_gateway_main_multi", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


main_multi = _load_module(_MAIN_PATH)


@pytest.fixture
def multi_run_output() -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        main_multi.main()
    return buf.getvalue()


def test_runs_exactly_four_turns(multi_run_output: str) -> None:
    """The transcript shape is 4 turns; each must produce its own banner."""
    for turn in (1, 2, 3, 4):
        assert f"Turn {turn}" in multi_run_output, f"Turn {turn} banner missing"
    assert "turns                       = 4" in multi_run_output


def test_all_four_intents_hit_the_shortlist(multi_run_output: str) -> None:
    """Each turn's intent must rank into the bounded shortlist — proves the
    routing config can serve a realistic conversational shape, not just
    cherry-picked queries."""
    assert "intent_in_shortlist_count   = 4 / 4" in multi_run_output


def test_facts_accumulate_monotonically(multi_run_output: str) -> None:
    """One fact written per turn; total fact count grows 1, 2, 3, 4."""
    assert "facts_per_turn              = [1, 2, 3, 4]" in multi_run_output


def test_turn1_artifact_persists_into_final_prompt(multi_run_output: str) -> None:
    """The load-bearing multi-turn claim: dependency closure carries the
    turn-1 BigQuery artifact reference all the way to turn 4's answer
    prompt. If this regresses, multi-turn agents lose their evidence
    chain."""
    assert "turn1_artifact_in_final_prompt = yes" in multi_run_output
    assert "artifact_handles_persisted  = ['artifact:result:tc1']" in multi_run_output


def test_answer_tokens_grow_but_stay_bounded(multi_run_output: str) -> None:
    """Per-turn answer-prompt tokens grow as facts accumulate, but each
    turn's token cost stays well inside the 4000-token answer budget."""
    match = re.search(r"answer_tokens_per_turn\s*=\s*\[([\d, ]+)\]", multi_run_output)
    assert match is not None, "per-turn token line missing"
    tokens = [int(t) for t in match.group(1).split(",")]
    assert len(tokens) == 4
    assert all(t > 0 for t in tokens)
    # No turn blows the 4000-token answer budget configured in main_multi.
    assert all(t < 4000 for t in tokens)
    # The list must be monotonically non-decreasing — facts only grow.
    for prev, curr in zip(tokens, tokens[1:], strict=False):
        assert curr >= prev, f"answer tokens regressed across turns: {tokens}"


def test_turn1_firewall_collapses_bigquery_rowset(multi_run_output: str) -> None:
    """Turn 1 ingests the same 16 KB rowset as `main.py`; the firewall
    summary must stay well under 500 chars."""
    turn1_section = multi_run_output.split("Turn 2", 1)[0]
    match = re.search(r"firewall: ([\d,]+) chars -> (\d+) chars", turn1_section)
    assert match is not None, "turn-1 firewall narration missing"
    raw_chars = int(match.group(1).replace(",", ""))
    summary_chars = int(match.group(2))
    assert raw_chars > 10_000
    assert summary_chars < 500


def test_only_one_artifact_persists_across_four_turns(multi_run_output: str) -> None:
    """Of the four tool calls, only the BigQuery rowset is large enough to
    cross the firewall threshold — turns 2-4 stay inline. This pins the
    "firewall correctly no-ops on small inputs" guarantee that turns 2-4
    exercise."""
    match = re.search(r"artifact_handles_persisted\s*=\s*(\[[^\]]*\])", multi_run_output)
    assert match is not None
    assert match.group(1) == "['artifact:result:tc1']"


def test_top_k_shortlist_size_is_consistent_across_turns(multi_run_output: str) -> None:
    """Every turn renders 5 ChoiceCards — the top_k=5 routing config
    applies uniformly, not just on turn 1."""
    card_lines = re.findall(r"cards:\s+(\d+)", multi_run_output)
    assert len(card_lines) == 4
    assert all(c == "5" for c in card_lines)
