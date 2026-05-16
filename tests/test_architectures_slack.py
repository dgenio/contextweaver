"""Smoke test for the Slack ops bot reference architecture (#198).

The architecture is exercised by ``make example`` via the ``architectures``
umbrella target. This unit test pins the deterministic invariants of the
scripted transcript so regressions in routing, the firewall, or fact
persistence surface immediately rather than after a CI ``make example``
failure.

The transcript is deterministic — no randomness, no real network calls —
so we can assert specific numbers (intent match count, fact keys, firewall
artifact count, large-result threshold) rather than just "ran without
exception".
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

# The architecture lives in examples/, not under src/. Add it to sys.path
# for direct import.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "examples" / "architectures" / "slack_ops_bot"))

import main as slack_ops_bot  # noqa: E402  (import after sys.path manipulation)


@pytest.fixture
def slack_run_output() -> str:
    """Run ``main()`` once and capture stdout for assertions."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        slack_ops_bot.main()
    return buf.getvalue()


def test_catalog_loads_with_expected_size(slack_run_output: str) -> None:
    """The committed catalog.yaml ships 48 tools."""
    assert "Loaded catalog: 48 tools" in slack_run_output


def test_all_six_transcript_turns_run(slack_run_output: str) -> None:
    """All six scripted turns produce a ``Turn N`` banner."""
    for n in range(1, 7):
        assert f"Turn {n}" in slack_run_output


def test_every_intent_lands_in_router_shortlist(slack_run_output: str) -> None:
    """The deterministic transcript was tuned so each intent is in the top-3."""
    assert "intent in router top-3: 6/6  (100%)" in slack_run_output
    assert "NOT in shortlist" not in slack_run_output


def test_firewall_fires_on_the_large_log_dump(slack_run_output: str) -> None:
    """Turn 2's ``logs.tail`` exceeds 2000 chars, so the firewall reports its action."""
    # Pin the order of magnitude (~34 KB) rather than the exact char count so a
    # downstream `json.dumps` representation change or a tweak to the log-dump
    # structure doesn't break this test.
    assert "firewall: 34," in slack_run_output
    assert "char summary" in slack_run_output
    assert "artifact " in slack_run_output


def test_persistent_facts_carry_across_turns(slack_run_output: str) -> None:
    """All three expected fact keys land in the FactStore by end of run."""
    assert "oncall.api-gateway = alice@example.com" in slack_run_output
    assert "deploy.api-gateway = rolled back from 9f12abc to 8a01def" in slack_run_output
    assert "incident.api-gateway = OPS-4821" in slack_run_output


def test_final_prompt_renders_facts_section(slack_run_output: str) -> None:
    """Persisted facts appear in the final answer-phase prompt under [FACTS]."""
    assert "[FACTS]" in slack_run_output
    final_section = slack_run_output.split("Final answer-phase prompt", 1)[1]
    assert "oncall.api-gateway: alice@example.com" in final_section
    assert "incident.api-gateway: OPS-4821" in final_section


def test_select_from_shortlist_prefers_intent_when_present() -> None:
    """The intent-map helper picks the intent if it is in the shortlist."""
    assert slack_ops_bot._select_from_shortlist(["a.x", "b.y", "c.z"], "b.y") == "b.y"


def test_select_from_shortlist_falls_back_to_top_when_missing() -> None:
    """The helper falls back to the top-1 candidate when the intent is absent."""
    assert slack_ops_bot._select_from_shortlist(["a.x", "b.y", "c.z"], "absent") == "a.x"
