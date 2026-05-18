"""Smoke test for the voice agent reference architecture (#205).

The architecture is exercised by ``make example`` via the ``architectures``
umbrella target. This unit test pins the deterministic invariants of the
scripted call so regressions in routing, the async build pattern, or
fact persistence surface immediately rather than after a CI
``make example`` failure.

The call is deterministic — no randomness, no real network calls — so
we can assert specific numbers (intent match count, fact keys, max
answer token bound) rather than just "ran without exception".

Wall-clock timings reported by the example (``X.X ms off-thread``) are
**not** asserted on: they vary between machines and CI runners.
"""

from __future__ import annotations

import importlib.util
import io
from contextlib import redirect_stdout
from pathlib import Path

import pytest

# Load the architecture under a unique module name so it can coexist with
# the other architectures in the same pytest run — a bare ``import main``
# from sys.path collides across the three architecture test files.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_MAIN_PATH = _REPO_ROOT / "examples" / "architectures" / "voice_agent" / "main.py"
_spec = importlib.util.spec_from_file_location("voice_agent_main", _MAIN_PATH)
assert _spec is not None and _spec.loader is not None
voice_agent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(voice_agent)


@pytest.fixture
def voice_run_output() -> str:
    """Run ``main()`` once and capture stdout for assertions."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        voice_agent.main()
    return buf.getvalue()


def test_catalog_loads_with_expected_size(voice_run_output: str) -> None:
    """The committed catalog.yaml ships 18 tools."""
    assert "Loaded catalog: 18 tools" in voice_run_output


def test_all_five_turns_run(voice_run_output: str) -> None:
    """All five scripted turns produce a ``Turn N`` banner."""
    for n in range(1, 6):
        assert f"Turn {n}" in voice_run_output


def test_every_intent_lands_in_router_shortlist(voice_run_output: str) -> None:
    """The deterministic transcript was tuned so each intent is in the top-3."""
    assert "intent in router top-3: 5/5  (100%)" in voice_run_output
    assert "NOT in shortlist" not in voice_run_output


def test_async_build_pattern_reported(voice_run_output: str) -> None:
    """Every turn reports an ``off-thread`` timing — the asyncio.to_thread pattern fired.

    We don't assert on the millisecond figure (it varies by machine);
    we only assert that the off-thread marker appears for every turn's
    answer-phase build.
    """
    assert voice_run_output.count("ms off-thread") >= 5


def test_persistent_facts_carry_across_turns(voice_run_output: str) -> None:
    """All three expected fact keys land in the FactStore by end of run."""
    assert "customer.order_id = A-481" in voice_run_output
    assert "customer.shipping_address = 42 Apple St, Springfield" in voice_run_output
    assert "customer.callback = 2026-05-17T14:00 (PT)" in voice_run_output


def test_final_prompt_renders_facts_section(voice_run_output: str) -> None:
    """Persisted facts appear in the final answer-phase prompt under [FACTS]."""
    assert "[FACTS]" in voice_run_output
    final_section = voice_run_output.split("Final answer-phase prompt", 1)[1]
    assert "customer.order_id: A-481" in final_section
    assert "customer.shipping_address: 42 Apple St, Springfield" in final_section
    assert "customer.callback: 2026-05-17T14:00 (PT)" in final_section


def test_answer_prompt_respects_tight_voice_budget(voice_run_output: str) -> None:
    """Max answer-prompt tokens must stay under the documented 1000-token budget."""
    # Pull the scoreboard line: ``max answer-prompt tokens: NNN (budget=1000)``.
    line = next(ln for ln in voice_run_output.splitlines() if "max answer-prompt tokens:" in ln)
    # Parse: take the integer after the colon.
    value = int(line.split("max answer-prompt tokens:")[1].split("(")[0].strip())
    # Tight assertion: at five turns the prompt is comfortably under budget.
    # If a future tweak pushed this over ~400 tokens, the architecture's
    # latency story would regress — that's the invariant we're guarding.
    assert value < 400, f"answer prompt grew unexpectedly: {value} tokens"


def test_pipecat_reported_as_optional_dep(voice_run_output: str) -> None:
    """The example always reports the Pipecat install state.

    Whether True or False — the example must say so explicitly so a
    reader knows the optional-extra path was checked.
    """
    assert "pipecat-ai installed:" in voice_run_output


def test_select_from_shortlist_prefers_intent_when_present() -> None:
    """The intent-map helper picks the intent if it is in the shortlist."""
    assert voice_agent._select_from_shortlist(["a.x", "b.y", "c.z"], "b.y") == "b.y"


def test_select_from_shortlist_falls_back_to_top_when_missing() -> None:
    """The helper falls back to the top-1 candidate when the intent is absent."""
    assert voice_agent._select_from_shortlist(["a.x", "b.y", "c.z"], "absent") == "a.x"
