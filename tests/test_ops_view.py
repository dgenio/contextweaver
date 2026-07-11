"""Tests for the read-only gateway ops view (issue #668)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rich.console import Console

from contextweaver.diagnostics import DiagnosticEvent
from contextweaver.ops_view import build_snapshot, render_text, watch_loop

_BASE = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)


def _event(
    name: str,
    *,
    seconds: float = 0.0,
    success: bool = True,
    duration_ms: float | None = None,
    tool_id: str | None = None,
) -> DiagnosticEvent:
    return DiagnosticEvent(
        event=name,
        timestamp=(_BASE + timedelta(seconds=seconds)).isoformat(),
        success=success,
        duration_ms=duration_ms,
        session_id="s1",
        tool_id=tool_id,
    )


def _fixture_events() -> list[DiagnosticEvent]:
    events = [
        _event("browse.completed", seconds=0, duration_ms=10.0),
        _event("browse.completed", seconds=1, duration_ms=20.0),
        _event("execute.completed", seconds=2, duration_ms=100.0, tool_id="fs::read"),
        _event("execute.completed", seconds=3, duration_ms=200.0, tool_id="fs::read"),
        _event("execute.failed", seconds=4, success=False, duration_ms=300.0, tool_id="net::call"),
        _event("view.completed", seconds=5, tool_id="fs::read"),
    ]
    return events


def test_snapshot_math_exact() -> None:
    snapshot = build_snapshot(_fixture_events(), window_seconds=900)
    assert snapshot.events_total == 6
    assert snapshot.error_count == 1
    assert snapshot.error_rate == 1 / 6
    assert snapshot.view_count == 1
    # Durations: [10, 20, 100, 200, 300] → nearest-rank p50=100, p95=300.
    assert snapshot.latency_p50_ms == 100.0
    assert snapshot.latency_p95_ms == 300.0
    assert snapshot.top_executed == [("fs::read", 2), ("net::call", 1)]
    assert snapshot.top_failures == [("net::call", 1)]
    assert snapshot.family_counts["route_request"] == 2
    assert snapshot.family_counts["execution"] == 3
    assert snapshot.last_errors == [("execute.failed", "net::call")]


def test_window_filters_old_events() -> None:
    events = _fixture_events()
    now = (_BASE + timedelta(seconds=5)).timestamp()
    snapshot = build_snapshot(events, window_seconds=2.5, now=now)
    assert snapshot.events_total == 3  # seconds 3, 4, 5 only


def test_deterministic_and_serde() -> None:
    first = build_snapshot(_fixture_events(), window_seconds=900)
    second = build_snapshot(_fixture_events(), window_seconds=900)
    assert first.to_dict() == second.to_dict()
    assert isinstance(first.to_dict()["top_executed"][0], list)


def test_render_text_contains_key_figures() -> None:
    text = render_text(build_snapshot(_fixture_events(), window_seconds=900))
    assert "events: 6" in text
    assert "errors: 1" in text
    assert "fs::read (2)" in text
    assert "p95 300.0ms" in text
    assert "execute.failed net::call" in text


def test_empty_stream_renders_cleanly() -> None:
    snapshot = build_snapshot([], window_seconds=900)
    assert snapshot.events_total == 0 and snapshot.error_rate == 0.0
    assert "events: 0" in render_text(snapshot)


def test_watch_loop_picks_up_appended_events(tmp_path: Path) -> None:
    target = tmp_path / "diag.jsonl"
    events = _fixture_events()
    target.write_text(json.dumps(events[0].to_dict()) + "\n", encoding="utf-8")

    appended = {"done": False}

    def fake_sleep(_: float) -> None:
        if not appended["done"]:
            with target.open("a", encoding="utf-8") as handle:
                for event in events[1:]:
                    handle.write(json.dumps(event.to_dict()) + "\n")
                handle.write("{malformed json\n")
            appended["done"] = True

    console = Console(record=True, width=100)
    snapshot = watch_loop(
        target, iterations=2, interval_seconds=0.01, console=console, sleep=fake_sleep
    )
    assert snapshot.events_total == 6  # malformed line skipped, appends picked up
    output = console.export_text()
    assert "gateway ops" in output


def test_watch_loop_missing_file_is_tolerated(tmp_path: Path) -> None:
    console = Console(record=True, width=100)
    snapshot = watch_loop(
        tmp_path / "absent.jsonl", iterations=1, console=console, sleep=lambda _: None
    )
    assert snapshot.events_total == 0
