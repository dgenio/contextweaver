"""Tests for contextweaver.visualize."""

from __future__ import annotations

from contextweaver.context.explanation import CandidateExplanation, ContextBuildExplanation
from contextweaver.diagnostics import DiagnosticEvent
from contextweaver.envelope import BuildStats, DroppedItem, FirewallStats
from contextweaver.routing.router import Router
from contextweaver.routing.trace import RouteTrace
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import SelectableItem
from contextweaver.visualize import render_build_html, render_route_html, render_session_html

_MALICIOUS = "<script>alert(1)</script>"


def _route_trace() -> RouteTrace:
    items = [
        SelectableItem(id="db_read", kind="tool", name="read_db", description="Read from database"),
        SelectableItem(
            id="send_email",
            kind="tool",
            name="send_email",
            description=f"Send email {_MALICIOUS}",
        ),
    ]
    graph = TreeBuilder(max_children=20).build(items)
    router = Router(graph, items=items, top_k=5)
    return router.route(f"read database {_MALICIOUS}", debug=True).trace


def _build_stats() -> BuildStats:
    return BuildStats(
        tokens_per_section={"tool_result": 120, "memory_fact": 30},
        total_candidates=5,
        included_count=3,
        dropped_count=2,
        dropped_reasons={"budget": 1, "sensitivity": 1},
        dropped_items=[
            DroppedItem(item_id=f"evil-{_MALICIOUS}", reason="budget"),
            DroppedItem(item_id="item-2", reason="sensitivity"),
        ],
        token_estimator="heuristic/v2",
        firewall_events=[
            FirewallStats(
                triggered=True,
                strategy="summary",
                original_tokens=500,
                summary_tokens=50,
                artifact_ref="artifact:result:1",
            )
        ],
    )


def _explanation() -> ContextBuildExplanation:
    return ContextBuildExplanation(
        phase="answer",
        query="q",
        total_candidates=2,
        included_count=1,
        dropped_count=1,
        candidates=[
            CandidateExplanation(
                item_id="kept-1", kind="tool_result", sensitivity="public", score=0.9, included=True
            ),
            CandidateExplanation(
                item_id=f"drop-{_MALICIOUS}",
                kind="tool_result",
                sensitivity="internal",
                score=0.2,
                drop_reason="budget",
            ),
        ],
    )


def _events() -> list[DiagnosticEvent]:
    return [
        DiagnosticEvent(
            event="execute.completed",
            timestamp="2026-07-01T00:00:00+00:00",
            duration_ms=12.5,
            session_id="s1",
            tool_id=f"tool-{_MALICIOUS}",
            namespace="github",
            attributes={"raw_tokens": 100},
        ),
        DiagnosticEvent(
            event="execute.failed",
            timestamp="2026-07-01T00:00:01+00:00",
            success=False,
            duration_ms=3.0,
            session_id="s1",
            tool_id="github.search",
        ),
        DiagnosticEvent(event="browse.completed", timestamp="2026-07-01T00:00:02+00:00"),
    ]


def _assert_safe(page: str) -> None:
    assert _MALICIOUS not in page
    assert "&lt;script&gt;" in page
    assert "http://" not in page
    assert "https://" not in page


def test_route_html_contains_ids_and_is_escaped() -> None:
    trace = _route_trace()
    page = render_route_html(trace)
    assert "db_read" in page
    assert trace.retriever_engine in page
    _assert_safe(page)


def test_route_html_byte_identical_across_renders() -> None:
    trace = _route_trace()
    assert render_route_html(trace) == render_route_html(trace)


def test_route_html_without_steps_mentions_debug() -> None:
    page = render_route_html(RouteTrace(query="plain"))
    assert "debug=True" in page


def test_build_html_contains_stats_and_is_escaped() -> None:
    page = render_build_html(_build_stats(), explanation=_explanation())
    assert "tool_result" in page
    assert "heuristic/v2" in page
    assert "kept-1" in page
    assert "budget" in page
    _assert_safe(page)


def test_build_html_byte_identical_across_renders() -> None:
    stats = _build_stats()
    explanation = _explanation()
    first = render_build_html(stats, explanation=explanation)
    second = render_build_html(stats, explanation=explanation)
    assert first == second


def test_build_html_without_explanation_omits_candidates() -> None:
    page = render_build_html(_build_stats())
    assert "Candidates</h2>" not in page
    assert "Dropped items" in page


def test_session_html_timeline_and_family_counts() -> None:
    page = render_session_html(_events())
    assert "execute" in page
    assert "browse" in page
    assert "2026-07-01T00:00:00+00:00" in page
    assert "github.search" in page
    assert ">fail<" in page
    _assert_safe(page)


def test_session_html_byte_identical_and_no_own_timestamps() -> None:
    events = _events()
    assert render_session_html(events) == render_session_html(events)
    empty = render_session_html([])
    assert "No events." in empty
    assert "2026" not in empty


def test_no_external_resources_or_scripts() -> None:
    for page in (
        render_route_html(_route_trace()),
        render_build_html(_build_stats()),
        render_session_html(_events()),
    ):
        assert "<script" not in page
        assert "src=" not in page
        assert "@import" not in page
        assert "url(" not in page
