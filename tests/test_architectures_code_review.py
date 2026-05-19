"""Smoke test for the code-review bot reference architecture (#204).

The architecture is exercised by ``make example`` via the ``architectures``
umbrella target. This unit test pins the deterministic invariants of the
scripted review so regressions in routing, the firewall, or fact
persistence surface immediately rather than after a CI ``make example``
failure.

The review is deterministic — no randomness, no real network calls — so
we can assert specific numbers (intent match count, fact keys, firewall
artifact count, large-result threshold) rather than just "ran without
exception".
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
_MAIN_PATH = _REPO_ROOT / "examples" / "architectures" / "code_review_bot" / "main.py"
_spec = importlib.util.spec_from_file_location("code_review_bot_main", _MAIN_PATH)
assert _spec is not None and _spec.loader is not None
code_review_bot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(code_review_bot)


@pytest.fixture
def code_review_run_output() -> str:
    """Run ``main()`` once and capture stdout for assertions."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        code_review_bot.main()
    return buf.getvalue()


def test_catalog_loads_with_expected_size(code_review_run_output: str) -> None:
    """The committed catalog.yaml ships 24 tools."""
    assert "Loaded catalog: 24 tools" in code_review_run_output


def test_all_six_review_steps_run(code_review_run_output: str) -> None:
    """All six scripted review steps produce a ``Step N`` banner."""
    for n in range(1, 7):
        assert f"Step {n}" in code_review_run_output


def test_every_intent_lands_in_router_shortlist(code_review_run_output: str) -> None:
    """The deterministic transcript was tuned so each intent is in the top-3."""
    assert "intent in router top-3: 6/6  (100%)" in code_review_run_output
    assert "NOT in shortlist" not in code_review_run_output


def test_firewall_fires_twice(code_review_run_output: str) -> None:
    """The diff dump (~28 KB) and the grep result (~2.5 KB) both fire the firewall."""
    assert "firewall fires: 2/6" in code_review_run_output
    # The diff and grep payloads are pinned in shape (order-of-magnitude
    # assertions so we don't regress on tiny refactors of the canned data).
    assert "firewall: 24," in code_review_run_output  # ~24 KB diff
    assert "firewall: 2," in code_review_run_output  # ~2 KB grep
    assert "char summary" in code_review_run_output
    assert "artifact " in code_review_run_output


def test_artifact_store_holds_every_tool_result(code_review_run_output: str) -> None:
    """Every tool call writes an artifact, even when the firewall does not fire.

    This is the contract the drilldown story depends on: small results are
    still addressable.
    """
    assert "artifacts kept: 6" in code_review_run_output


def test_persistent_facts_carry_across_review_steps(code_review_run_output: str) -> None:
    """All three expected fact keys land in the FactStore by end of run."""
    assert "pr.target_file = payments/charge.py" in code_review_run_output
    assert "pr.test_status = 2 failed" in code_review_run_output
    assert "pr.type_errors = 2 errors" in code_review_run_output


def test_final_prompt_renders_facts_section(code_review_run_output: str) -> None:
    """Persisted facts appear in the final answer-phase prompt under [FACTS]."""
    assert "[FACTS]" in code_review_run_output
    final_section = code_review_run_output.split("Final answer-phase prompt", 1)[1]
    assert "pr.target_file: payments/charge.py" in final_section
    assert "pr.test_status:" in final_section
    assert "pr.type_errors:" in final_section


def test_lint_run_routes_to_lint_namespace(code_review_run_output: str) -> None:
    """Step 5 must route to lint.run, not test.coverage — guards against
    a regression we previously had to fix by adding 'ruff' to the lint.run
    description so TF-IDF picks it up.
    """
    # The line that records the chosen tool for step 5.
    step5_section = code_review_run_output.split("Step 5", 1)[1].split("Step 6", 1)[0]
    assert "chosen:   lint.run" in step5_section


def test_select_from_shortlist_prefers_intent_when_present() -> None:
    """The intent-map helper picks the intent if it is in the shortlist."""
    assert code_review_bot._select_from_shortlist(["a.x", "b.y", "c.z"], "b.y") == "b.y"


def test_select_from_shortlist_falls_back_to_top_when_missing() -> None:
    """The helper falls back to the top-1 candidate when the intent is absent."""
    assert code_review_bot._select_from_shortlist(["a.x", "b.y", "c.z"], "absent") == "a.x"
