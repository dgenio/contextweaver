"""Structured, privacy-conscious diagnostics for gateway operations.

The gateway emits versioned :class:`DiagnosticEvent` records to an injected
:class:`DiagnosticSink`. The built-in :class:`JsonlDiagnosticSink` provides an
append-only local event log; :func:`summarize_diagnostics` turns that stream
into operator-facing counts, savings, latency percentiles, and drill-down IDs.

Events intentionally carry metadata only. Runtime instrumentation records
identifiers, sizes, timings, argument key names, and error codes, never query
text, argument values, result text, or artifact bytes.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from contextweaver.exceptions import ConfigError

DIAGNOSTIC_EVENT_VERSION: int = 1
DIAGNOSTIC_REPORT_VERSION: int = 1


def utc_timestamp() -> str:
    """Return the current UTC timestamp in ISO-8601 form."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DiagnosticEvent:
    """One structured gateway diagnostic event.

    Attributes:
        event: Stable event name such as ``"browse.completed"``.
        timestamp: UTC ISO-8601 timestamp.
        success: Whether the operation succeeded.
        duration_ms: Operation latency in milliseconds, when applicable.
        session_id: Runtime session identifier.
        tool_id: Canonical tool identifier, when applicable.
        namespace: Tool namespace, when applicable.
        attributes: JSON-compatible metadata. Callers must not place payload
            content or argument values here.
        version: Event schema version.
    """

    event: str
    timestamp: str = field(default_factory=utc_timestamp)
    success: bool = True
    duration_ms: float | None = None
    session_id: str = ""
    tool_id: str | None = None
    namespace: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    version: int = DIAGNOSTIC_EVENT_VERSION

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "version": self.version,
            "event": self.event,
            "timestamp": self.timestamp,
            "success": self.success,
            "duration_ms": self.duration_ms,
            "session_id": self.session_id,
            "tool_id": self.tool_id,
            "namespace": self.namespace,
            "attributes": dict(self.attributes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiagnosticEvent:
        """Deserialise from a JSON-compatible dict."""
        duration = data.get("duration_ms")
        return cls(
            version=int(data.get("version", DIAGNOSTIC_EVENT_VERSION)),
            event=str(data["event"]),
            timestamp=str(data.get("timestamp", "")),
            success=bool(data.get("success", True)),
            duration_ms=float(duration) if duration is not None else None,
            session_id=str(data.get("session_id", "")),
            tool_id=str(data["tool_id"]) if data.get("tool_id") is not None else None,
            namespace=str(data["namespace"]) if data.get("namespace") is not None else None,
            attributes=dict(data.get("attributes", {})),
        )


@runtime_checkable
class DiagnosticSink(Protocol):
    """Destination for structured diagnostic events."""

    def emit(self, event: DiagnosticEvent) -> None:
        """Persist or export *event*."""
        ...


class NoOpDiagnosticSink:
    """Default sink that discards events."""

    def emit(self, event: DiagnosticEvent) -> None:
        """Discard *event*."""
        _ = event


class InMemoryDiagnosticSink:
    """Thread-safe sink retaining events for tests and embedded dashboards."""

    def __init__(self) -> None:
        """Create an empty sink."""
        self._lock = threading.Lock()
        self._events: list[DiagnosticEvent] = []

    def emit(self, event: DiagnosticEvent) -> None:
        """Append *event*."""
        with self._lock:
            self._events.append(event)

    def events(self) -> list[DiagnosticEvent]:
        """Return a snapshot of retained events."""
        with self._lock:
            return list(self._events)


class JsonlDiagnosticSink:
    """Append diagnostic events to a UTF-8 JSONL file."""

    def __init__(self, path: str | Path) -> None:
        """Create a sink writing to *path*."""
        self.path = Path(path)
        if not self.path.parent.exists():
            raise ConfigError(f"diagnostics directory does not exist: {self.path.parent}")
        self._lock = threading.Lock()

    def emit(self, event: DiagnosticEvent) -> None:
        """Append one canonical JSON line."""
        line = json.dumps(event.to_dict(), sort_keys=True, separators=(",", ":"))
        with self._lock, self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(line + "\n")


def load_diagnostic_events(path: str | Path) -> list[DiagnosticEvent]:
    """Load a diagnostic JSONL stream.

    Raises:
        ConfigError: If a non-empty line is not a valid event object.
    """
    source = Path(path)
    events: list[DiagnosticEvent] = []
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ConfigError(f"cannot read diagnostics file {source}: {exc}") from exc
    for lineno, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise TypeError("event must be a JSON object")
            events.append(DiagnosticEvent.from_dict(raw))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ConfigError(f"{source}:{lineno}: invalid diagnostic event: {exc}") from exc
    return events


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(percentile * len(ordered) + 0.999999) - 1))
    return round(ordered[index], 3)


def summarize_diagnostics(events: list[DiagnosticEvent]) -> dict[str, Any]:
    """Aggregate diagnostic events into a deterministic gateway report."""
    names: dict[str, int] = {}
    namespaces: dict[str, int] = {}
    durations: list[float] = []
    failures = 0
    raw_tokens = 0
    compact_tokens = 0
    schema_tokens_avoided = 0
    artifact_views = 0
    tool_ids: set[str] = set()
    sessions: set[str] = set()

    for item in events:
        names[item.event] = names.get(item.event, 0) + 1
        if not item.success:
            failures += 1
        if item.duration_ms is not None:
            durations.append(item.duration_ms)
        if item.session_id:
            sessions.add(item.session_id)
        if item.tool_id:
            tool_ids.add(item.tool_id)
        if item.namespace:
            namespaces[item.namespace] = namespaces.get(item.namespace, 0) + 1
        raw_tokens += int(item.attributes.get("raw_tokens", 0))
        compact_tokens += int(item.attributes.get("compact_tokens", 0))
        schema_tokens_avoided += int(item.attributes.get("schema_tokens_avoided", 0))
        if item.event == "view.completed":
            artifact_views += 1

    return {
        "version": DIAGNOSTIC_REPORT_VERSION,
        "event_count": len(events),
        "session_count": len(sessions),
        "failure_count": failures,
        "events_by_name": dict(sorted(names.items())),
        "unique_tool_count": len(tool_ids),
        "namespaces": dict(sorted(namespaces.items())),
        "artifact_view_count": artifact_views,
        "raw_tokens": raw_tokens,
        "compact_tokens": compact_tokens,
        "tokens_saved": max(raw_tokens - compact_tokens, 0),
        "schema_tokens_avoided": schema_tokens_avoided,
        "latency_ms": {
            "count": len(durations),
            "p50": _percentile(durations, 0.50),
            "p95": _percentile(durations, 0.95),
            "max": round(max(durations), 3) if durations else 0.0,
        },
    }


def render_diagnostic_report(summary: dict[str, Any]) -> str:
    """Render a grep-friendly Markdown report from a summary payload."""
    latency = summary.get("latency_ms", {})
    lines = [
        "# Gateway Diagnostics",
        "",
        f"- Events: {summary.get('event_count', 0)}",
        f"- Sessions: {summary.get('session_count', 0)}",
        f"- Failures: {summary.get('failure_count', 0)}",
        f"- Unique tools: {summary.get('unique_tool_count', 0)}",
        f"- Artifact views: {summary.get('artifact_view_count', 0)}",
        f"- Result tokens: {summary.get('raw_tokens', 0)} raw -> "
        f"{summary.get('compact_tokens', 0)} compact "
        f"({summary.get('tokens_saved', 0)} saved)",
        f"- Schema tokens avoided: {summary.get('schema_tokens_avoided', 0)}",
        f"- Latency: p50={latency.get('p50', 0)}ms "
        f"p95={latency.get('p95', 0)}ms max={latency.get('max', 0)}ms",
        "",
        "## Events",
    ]
    for name, count in summary.get("events_by_name", {}).items():
        lines.append(f"- `{name}`: {count}")
    return "\n".join(lines) + "\n"
