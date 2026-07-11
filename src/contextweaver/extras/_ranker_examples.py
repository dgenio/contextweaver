"""Telemetry-to-training-example derivation for the tool ranker (issue #388).

Private helper for :mod:`contextweaver.extras.ranker` — keeps that module
within the project's 300-line ceiling (same pattern as
``extras/memory/_zep_common.py`` / ``routing/_index_codec.py``).  Not public
API; the public names (:class:`RankingExample`, :func:`examples_from_events`)
are re-exported from ``contextweaver.extras.ranker``.

No scikit-learn dependency — everything here works on the default install.

Privacy note: the built-in
:class:`~contextweaver.adapters.gateway_diagnostics.GatewayTelemetry` records
only ``query_chars`` (metadata-only contract), never query text.  Deriving
training examples therefore requires a sink that opts into recording
``attributes["query"]`` on browse events; :func:`examples_from_events` reads
that key defensively and skips events without it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from contextweaver.diagnostics import DiagnosticEvent

_EXECUTE_EVENTS = ("execute.completed", "execute.failed")


@dataclass
class RankingExample:
    """One (query, tool) training example derived from gateway telemetry.

    Attributes:
        query: The browse query text.
        tool_id: Canonical tool id the example scores.
        executed: Whether the tool was executed after the browse.
        success: Whether the execution succeeded (``False`` when not executed).
        latency_ms: Execution latency, when recorded.
        features: Feature map filled at featurize time (empty until then).
    """

    query: str
    tool_id: str
    executed: bool
    success: bool
    latency_ms: float | None = None
    features: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "query": self.query,
            "tool_id": self.tool_id,
            "executed": self.executed,
            "success": self.success,
            "latency_ms": self.latency_ms,
            "features": dict(self.features),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RankingExample:
        """Deserialise from a JSON-compatible dict."""
        latency = data.get("latency_ms")
        return cls(
            query=str(data["query"]),
            tool_id=str(data["tool_id"]),
            executed=bool(data.get("executed", False)),
            success=bool(data.get("success", False)),
            latency_ms=float(latency) if latency is not None else None,
            features={str(k): float(v) for k, v in data.get("features", {}).items()},
        )


def examples_from_events(events: list[DiagnosticEvent]) -> list[RankingExample]:
    """Derive ranking examples from browse/execute diagnostic event pairs.

    A ``browse.completed`` event carrying ``attributes["query"]`` (opt-in —
    see the module docstring) opens a window extending to the next browse in
    the same ``session_id``.  Tools executed in that window become positives
    (``executed=True`` with the event's success flag); shortlisted-but-not-
    executed tools (``attributes["tool_ids"]``) become negatives.  Events
    lacking the needed attributes are skipped.  Output order is deterministic:
    sessions sorted by id, tool ids sorted within each window.
    """
    sessions: dict[str, list[DiagnosticEvent]] = {}
    for event in events:
        sessions.setdefault(event.session_id, []).append(event)
    examples: list[RankingExample] = []
    for session_id in sorted(sessions):
        examples.extend(_session_examples(sessions[session_id]))
    return examples


def _session_examples(events: list[DiagnosticEvent]) -> list[RankingExample]:
    """Derive examples from one session's ordered event list."""
    out: list[RankingExample] = []
    for i, event in enumerate(events):
        if event.event != "browse.completed":
            continue
        query = event.attributes.get("query")
        if not isinstance(query, str) or not query.strip():
            continue
        raw = event.attributes.get("tool_ids")
        shortlist = [t for t in raw if isinstance(t, str)] if isinstance(raw, list) else []
        executed: dict[str, tuple[bool, float | None]] = {}
        for follow in events[i + 1 :]:
            if follow.event == "browse.completed":
                break
            if follow.event in _EXECUTE_EVENTS and follow.tool_id:
                executed.setdefault(follow.tool_id, (follow.success, follow.duration_ms))
        for tool_id in sorted(set(shortlist) | set(executed)):
            success, latency = executed.get(tool_id, (False, None))
            out.append(
                RankingExample(
                    query=query,
                    tool_id=tool_id,
                    executed=tool_id in executed,
                    success=success,
                    latency_ms=latency,
                )
            )
    return out


__all__ = ["RankingExample", "examples_from_events"]
