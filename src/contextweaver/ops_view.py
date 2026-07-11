"""Read-only gateway operations view for live triage (issue #668).

Builds a compact operational snapshot from the sanitized diagnostics JSONL
that ``mcp serve --diagnostics FILE`` appends (see ``docs/telemetry.md``),
and renders it as plain text or a Rich table.  ``watch_loop`` tails the file
incrementally and re-renders live — read-only by design: the view never
talks to the gateway process, only to its diagnostics file, so it is safe
to run during an incident.

Pure construction lives in :func:`build_snapshot` (deterministic for a fixed
event list and ``now``); rendering derives everything from the snapshot.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from contextweaver.diagnostics import DiagnosticEvent
from contextweaver.telemetry_contract import classify_event

#: Event-name prefixes whose durations are latency-interesting.
_LATENCY_PREFIXES: tuple[str, ...] = ("browse.", "execute.")

#: Errors kept in the snapshot's tail.
_LAST_ERRORS = 5


@dataclass
class OpsSnapshot:
    """One point-in-time operational summary of a gateway diagnostics stream.

    Attributes:
        window_seconds: Width of the observation window.
        events_total: Events inside the window.
        family_counts: Events per telemetry-contract family (sorted keys).
        error_count: Events with ``success=False``.
        error_rate: ``error_count / events_total`` (0.0 when empty).
        latency_p50_ms / latency_p95_ms: Percentiles over browse/execute
            durations, ``None`` when no timed events exist.
        top_executed: ``(tool_id, count)`` pairs, most-executed first.
        top_failures: ``(tool_id, count)`` pairs over failed executes.
        view_count: ``view.*`` events (artifact drill-down activity).
        last_errors: Up to five most recent ``(event, tool_id)`` failures.
    """

    window_seconds: float
    events_total: int = 0
    family_counts: dict[str, int] = field(default_factory=dict)
    error_count: int = 0
    error_rate: float = 0.0
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    top_executed: list[tuple[str, int]] = field(default_factory=list)
    top_failures: list[tuple[str, int]] = field(default_factory=list)
    view_count: int = 0
    last_errors: list[tuple[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "window_seconds": self.window_seconds,
            "events_total": self.events_total,
            "family_counts": dict(self.family_counts),
            "error_count": self.error_count,
            "error_rate": self.error_rate,
            "latency_p50_ms": self.latency_p50_ms,
            "latency_p95_ms": self.latency_p95_ms,
            "top_executed": [list(pair) for pair in self.top_executed],
            "top_failures": [list(pair) for pair in self.top_failures],
            "view_count": self.view_count,
            "last_errors": [list(pair) for pair in self.last_errors],
        }


def _parse_ts(timestamp: str) -> float | None:
    """ISO-8601 → epoch seconds, ``None`` when unparseable."""
    try:
        return datetime.fromisoformat(timestamp).timestamp()
    except ValueError:
        return None


def _percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile over non-empty *values* (deterministic)."""
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, round(fraction * (len(ordered) - 1))))
    return ordered[rank]


def build_snapshot(
    events: list[DiagnosticEvent],
    *,
    window_seconds: float = 900.0,
    now: float | None = None,
) -> OpsSnapshot:
    """Summarise *events* over the trailing window ending at *now*.

    ``now`` defaults to the newest event timestamp so a static file renders
    its own tail; pass an explicit value in live/watch use.
    """
    stamped: list[tuple[DiagnosticEvent, float]] = []
    for event in events:
        ts = _parse_ts(event.timestamp)
        if ts is not None:
            stamped.append((event, ts))
    if now is None:
        now = max((ts for _, ts in stamped), default=0.0)
    recent = [event for event, ts in stamped if now - ts <= window_seconds]
    snapshot = OpsSnapshot(window_seconds=window_seconds, events_total=len(recent))
    families: dict[str, int] = {}
    durations: list[float] = []
    executed: dict[str, int] = {}
    failures: dict[str, int] = {}
    for event in recent:
        family = classify_event(event) or "other"
        families[family] = families.get(family, 0) + 1
        if not event.success:
            snapshot.error_count += 1
            snapshot.last_errors.append((event.event, event.tool_id or ""))
        if event.duration_ms is not None and event.event.startswith(_LATENCY_PREFIXES):
            durations.append(event.duration_ms)
        if event.event.startswith("execute.") and event.tool_id:
            executed[event.tool_id] = executed.get(event.tool_id, 0) + 1
            if not event.success:
                failures[event.tool_id] = failures.get(event.tool_id, 0) + 1
        if event.event.startswith("view."):
            snapshot.view_count += 1
    snapshot.family_counts = dict(sorted(families.items()))
    snapshot.error_rate = snapshot.error_count / snapshot.events_total if recent else 0.0
    if durations:
        snapshot.latency_p50_ms = _percentile(durations, 0.50)
        snapshot.latency_p95_ms = _percentile(durations, 0.95)

    def by_count_then_id(pair: tuple[str, int]) -> tuple[int, str]:
        return (-pair[1], pair[0])

    snapshot.top_executed = sorted(executed.items(), key=by_count_then_id)[:5]
    snapshot.top_failures = sorted(failures.items(), key=by_count_then_id)[:5]
    snapshot.last_errors = snapshot.last_errors[-_LAST_ERRORS:]
    return snapshot


def render_text(snapshot: OpsSnapshot) -> str:
    """Render *snapshot* as a deterministic plain-text block."""
    lines = [
        f"gateway ops — last {int(snapshot.window_seconds)}s",
        f"events: {snapshot.events_total}   errors: {snapshot.error_count} "
        f"({snapshot.error_rate:.1%})   views: {snapshot.view_count}",
    ]
    if snapshot.latency_p50_ms is not None and snapshot.latency_p95_ms is not None:
        lines.append(
            f"latency (browse/execute): p50 {snapshot.latency_p50_ms:.1f}ms   "
            f"p95 {snapshot.latency_p95_ms:.1f}ms"
        )
    lines.append(
        "families: " + (", ".join(f"{k}={v}" for k, v in snapshot.family_counts.items()) or "none")
    )
    for title, pairs in (
        ("top executed", snapshot.top_executed),
        ("top failures", snapshot.top_failures),
    ):
        if pairs:
            lines.append(f"{title}: " + ", ".join(f"{tool} ({n})" for tool, n in pairs))
    if snapshot.last_errors:
        lines.append("recent errors:")
        lines.extend(f"  {event} {tool}".rstrip() for event, tool in snapshot.last_errors)
    return "\n".join(lines)


def render_table(snapshot: OpsSnapshot) -> Table:
    """Render *snapshot* as a Rich table (same data as :func:`render_text`)."""
    table = Table(title=f"gateway ops — last {int(snapshot.window_seconds)}s", expand=False)
    table.add_column("metric")
    table.add_column("value")
    table.add_row("events", str(snapshot.events_total))
    table.add_row("errors", f"{snapshot.error_count} ({snapshot.error_rate:.1%})")
    table.add_row("views", str(snapshot.view_count))
    if snapshot.latency_p50_ms is not None and snapshot.latency_p95_ms is not None:
        table.add_row(
            "latency p50/p95", f"{snapshot.latency_p50_ms:.1f} / {snapshot.latency_p95_ms:.1f} ms"
        )
    for family, count in snapshot.family_counts.items():
        table.add_row(f"family: {family}", str(count))
    for tool, count in snapshot.top_executed:
        table.add_row(f"executed: {tool}", str(count))
    for tool, count in snapshot.top_failures:
        table.add_row(f"failing: {tool}", str(count))
    return table


def _read_new_events(path: Path, offset: int) -> tuple[list[DiagnosticEvent], int]:
    """Read JSONL events appended past *offset*; skip malformed lines."""
    events: list[DiagnosticEvent] = []
    with path.open("rb") as handle:
        handle.seek(offset)
        payload = handle.read()
        offset = handle.tell()
    for line in payload.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            events.append(DiagnosticEvent.from_dict(json.loads(line)))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    return events, offset


def watch_loop(
    path: str | Path,
    *,
    window_seconds: float = 900.0,
    interval_seconds: float = 2.0,
    iterations: int | None = None,
    console: Console | None = None,
    sleep: Callable[[float], None] | None = None,
) -> OpsSnapshot:
    """Tail *path* and live-render the ops view; returns the final snapshot.

    Args:
        path: Diagnostics JSONL file (``mcp serve --diagnostics``).
        window_seconds: Trailing observation window.
        interval_seconds: Delay between reads.
        iterations: Number of refresh cycles (``None`` = until interrupted);
            tests pass a small number.
        console: Injected Rich console (tests use ``Console(record=True)``).
        sleep: Injectable delay function (defaults to :func:`time.sleep`).
    """
    import time

    console = console or Console(stderr=True)
    sleep = sleep or time.sleep
    target = Path(path)
    events: list[DiagnosticEvent] = []
    offset = 0
    cycle = 0
    snapshot = OpsSnapshot(window_seconds=window_seconds)
    while iterations is None or cycle < iterations:
        if target.exists():
            fresh, offset = _read_new_events(target, offset)
            events.extend(fresh)
        snapshot = build_snapshot(events, window_seconds=window_seconds)
        console.clear()
        console.print(render_table(snapshot))
        cycle += 1
        if iterations is None or cycle < iterations:
            sleep(interval_seconds)
    return snapshot


__all__ = [
    "OpsSnapshot",
    "build_snapshot",
    "render_table",
    "render_text",
    "watch_loop",
]
