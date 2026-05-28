"""Smoke test for the LangGraph agent-loop reference architecture (#326).

The architecture runs with or without LangGraph installed (guarded import +
hand-rolled fallback). These tests pin the deterministic invariants that
hold on *both* paths, plus the guarantee that the two paths produce
identical output apart from the one ``engine:`` banner line.

Token counts are not asserted (tokeniser-dependent); every asserted number
is character- or count-based.
"""

from __future__ import annotations

import importlib.util
import io
from contextlib import redirect_stdout
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MAIN_PATH = _REPO_ROOT / "examples" / "architectures" / "langgraph_agent_loop" / "main.py"


def _load_module(name: str) -> object:
    spec = importlib.util.spec_from_file_location(name, _MAIN_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


langgraph_agent_loop = _load_module("langgraph_agent_loop_main")


def _run(module: object) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        module.main()  # type: ignore[attr-defined]
    return buf.getvalue()


@pytest.fixture
def run_output() -> str:
    return _run(langgraph_agent_loop)


def test_catalog_loads_with_hero_tools(run_output: str) -> None:
    """The generated catalog plus the four hero tools total 36 tools."""
    assert "catalog: 36 tools across 9 namespaces" in run_output


def test_engine_line_is_one_of_two_known_values(run_output: str) -> None:
    assert ("agent loop engine: langgraph" in run_output) or (
        "agent loop engine: fallback (langgraph not installed)" in run_output
    )


def test_turn_one_routes_to_logs_search_and_fires_firewall(run_output: str) -> None:
    """Turn t1 picks the log tool and firewalls the ~21 KB dump to a summary."""
    t1 = run_output.split("Turn t1", 1)[1].split("Turn t2", 1)[0]
    assert "chosen: infra.logs_search  (intent='infra.logs_search', in shortlist)" in t1
    assert "firewall: 21,705 chars -> 49-char summary" in t1
    assert "route prompt:  naive all-tools 2,364 chars  ->  ChoiceCards 526 chars" in t1


def test_turn_two_routes_to_incident_and_retains_prior_context(run_output: str) -> None:
    """Turn t2 picks the incident tool; its answer carries all 6 events forward."""
    t2 = run_output.split("Turn t2", 1)[1]
    assert "chosen: incident.draft_note  (intent='incident.draft_note', in shortlist)" in t2
    # 3 events from t1 + 3 from t2 = cross-turn retention.
    assert "included=6" in t2


def test_closing_summary_present(run_output: str) -> None:
    assert "LangGraph owned the route -> execute -> answer control flow." in run_output


def test_langgraph_and_fallback_paths_agree(run_output: str) -> None:
    """The two execution paths must produce identical output bar the engine line."""
    real = _load_module("lg_real_path")
    fb = _load_module("lg_fallback_path")
    fb._HAS_LANGGRAPH = False  # type: ignore[attr-defined]

    def _strip_engine(text: str) -> str:
        return "\n".join(ln for ln in text.splitlines() if not ln.startswith("agent loop engine:"))

    assert _strip_engine(_run(real)) == _strip_engine(_run(fb))


def test_select_from_shortlist_prefers_intent_when_present() -> None:
    assert langgraph_agent_loop._select_from_shortlist(["a", "b"], "b") == "b"  # type: ignore[attr-defined]


def test_select_from_shortlist_falls_back_to_top_when_missing() -> None:
    assert langgraph_agent_loop._select_from_shortlist(["a", "b"], "z") == "a"  # type: ignore[attr-defined]
