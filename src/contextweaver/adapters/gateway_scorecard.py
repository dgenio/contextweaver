"""Tool-surface health scorecard over catalog + gateway telemetry (issue #380).

:func:`build_scorecard` joins a :class:`~contextweaver.types.SelectableItem`
catalog with :class:`~contextweaver.diagnostics.DiagnosticEvent` streams (e.g.
loaded via :func:`contextweaver.telemetry_contract.read_jsonl`) into one
deterministic governance :class:`Scorecard`: inventory counts (issue #377),
usage/latency/failure hot-spots from execution-family events, routing exposure
from ``browse.completed`` ``tool_ids``, and duplicate-capability counts reused
from :func:`contextweaver.routing.collision.analyze_collisions`.  Derivations
use only real event attributes — the ``duration_ms``/``success`` envelope
fields and ``attributes["tool_ids"]`` on browse events; every execution-family
event (incl. ``execute.dry_run``/``execute.cache_hit``) counts as one call.
"""

from __future__ import annotations

import csv
import io
import json
import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from contextweaver.diagnostics import DiagnosticEvent
from contextweaver.exceptions import ConfigError
from contextweaver.routing.catalog_metadata import inventory_of, is_deprecated
from contextweaver.routing.collision import analyze_collisions
from contextweaver.telemetry_contract import classify_event
from contextweaver.types import SelectableItem

SCORECARD_VERSION: int = 1

#: Bucket used when an inventory dimension is absent (absence carries no judgment).
UNKNOWN: str = "unknown"


def _p95(values: list[float]) -> float:
    """Nearest-rank p95 (same convention as ``diagnostics._percentile``)."""
    ordered = sorted(values)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return round(ordered[index], 3)


def _top(counter: Counter[str], top_n: int) -> list[dict[str, Any]]:
    """Top-*n* ``{"tool_id", "count"}`` rows, ties broken by ``tool_id``."""
    ranked = sorted(counter.items(), key=lambda pair: (-pair[1], pair[0]))[:top_n]
    return [{"tool_id": tool_id, "count": count} for tool_id, count in ranked]


@dataclass
class Scorecard:
    """Deterministic result of :func:`build_scorecard` (issue #380).

    Attributes:
        total_tools / total_namespaces: Catalog totals.
        by_owner / by_domain / by_risk / by_lifecycle: Item counts per sorted
            inventory dimension (issue #377); missing values → :data:`UNKNOWN`.
        most_executed: Top-N ``{"tool_id", "count"}`` over execution-family
            events carrying a ``tool_id``.
        most_routed: Top-N ``{"tool_id", "count"}`` over browse ``tool_ids``.
        unused_tools: Sorted catalog ids with zero executions.
        deprecated_in_use: Sorted executed ``lifecycle="deprecated"`` ids.
        highest_latency: Top-N ``{"tool_id", "p95_ms", "calls"}`` (min 1 timed
            call).
        highest_failure_rate: Top-N ``{"tool_id", "failure_rate", "calls"}``
            (min 3 calls; failures are ``success=False`` events).
        largest_schema: Top-N ``{"tool_id", "schema_chars"}`` by serialized
            ``args_schema`` size.
        selected_not_executed: Sorted routed ids never executed.
        collision_counts: Per-kind counts from ``analyze_collisions``.
    """

    total_tools: int = 0
    total_namespaces: int = 0
    by_owner: dict[str, int] = field(default_factory=dict)
    by_domain: dict[str, int] = field(default_factory=dict)
    by_risk: dict[str, int] = field(default_factory=dict)
    by_lifecycle: dict[str, int] = field(default_factory=dict)
    most_executed: list[dict[str, Any]] = field(default_factory=list)
    most_routed: list[dict[str, Any]] = field(default_factory=list)
    unused_tools: list[str] = field(default_factory=list)
    deprecated_in_use: list[str] = field(default_factory=list)
    highest_latency: list[dict[str, Any]] = field(default_factory=list)
    highest_failure_rate: list[dict[str, Any]] = field(default_factory=list)
    largest_schema: list[dict[str, Any]] = field(default_factory=list)
    selected_not_executed: list[str] = field(default_factory=list)
    collision_counts: dict[str, int] = field(default_factory=dict)
    version: int = SCORECARD_VERSION

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "version": self.version,
            "total_tools": self.total_tools,
            "total_namespaces": self.total_namespaces,
            "by_owner": dict(self.by_owner),
            "by_domain": dict(self.by_domain),
            "by_risk": dict(self.by_risk),
            "by_lifecycle": dict(self.by_lifecycle),
            "most_executed": [dict(row) for row in self.most_executed],
            "most_routed": [dict(row) for row in self.most_routed],
            "unused_tools": list(self.unused_tools),
            "deprecated_in_use": list(self.deprecated_in_use),
            "highest_latency": [dict(row) for row in self.highest_latency],
            "highest_failure_rate": [dict(row) for row in self.highest_failure_rate],
            "largest_schema": [dict(row) for row in self.largest_schema],
            "selected_not_executed": list(self.selected_not_executed),
            "collision_counts": dict(self.collision_counts),
        }


def _dimension(inventories: list[Any], attr: str) -> dict[str, int]:
    """Count items per inventory *attr* value (``None``/absent → :data:`UNKNOWN`)."""
    counter: Counter[str] = Counter()
    for inventory in inventories:
        counter[(getattr(inventory, attr) if inventory else None) or UNKNOWN] += 1
    return dict(sorted(counter.items()))


def _rank(rows: list[dict[str, Any]], key: str, top_n: int) -> list[dict[str, Any]]:
    """Sort *rows* by ``(-row[key], tool_id)`` and keep the top *top_n*."""
    return sorted(rows, key=lambda row: (-row[key], row["tool_id"]))[:top_n]


def build_scorecard(
    items: list[SelectableItem],
    events: list[DiagnosticEvent],
    *,
    top_n: int = 5,
) -> Scorecard:
    """Build a tool-surface health :class:`Scorecard` from *items* and *events*.

    Args:
        items: The tool catalog (never mutated).
        events: Diagnostic events; only execution- and browse-family events
            (per :func:`~contextweaver.telemetry_contract.classify_event`)
            contribute, others are ignored.
        top_n: Row cap for every ranked list.

    Returns:
        A deterministic, fully-populated :class:`Scorecard`.

    Raises:
        ConfigError: If *top_n* is not positive, or an inventory payload is
            corrupt (:func:`inventory_of` fails loudly).
    """
    if top_n < 1:
        raise ConfigError("build_scorecard() requires top_n >= 1")
    executed: Counter[str] = Counter()
    routed: Counter[str] = Counter()
    failures: Counter[str] = Counter()
    durations: dict[str, list[float]] = {}
    for event in events:
        family = classify_event(event)
        if family == "execution" and event.tool_id:
            executed[event.tool_id] += 1
            if not event.success:
                failures[event.tool_id] += 1
            if event.duration_ms is not None:
                durations.setdefault(event.tool_id, []).append(event.duration_ms)
        elif family == "route_request":
            tool_ids = event.attributes.get("tool_ids")
            if isinstance(tool_ids, list):
                routed.update(tid for tid in tool_ids if isinstance(tid, str))
    inventories = [inventory_of(item) for item in items]
    latency = [
        {"tool_id": tid, "p95_ms": _p95(vals), "calls": len(vals)}
        for tid, vals in durations.items()
    ]
    failure_rate = [
        {"tool_id": tid, "failure_rate": round(failures[tid] / calls, 4), "calls": calls}
        for tid, calls in executed.items()
        if calls >= 3
    ]
    schema_sizes = [
        {"tool_id": item.id, "schema_chars": len(json.dumps(item.args_schema, sort_keys=True))}
        for item in items
    ]
    return Scorecard(
        total_tools=len(items),
        total_namespaces=len({item.namespace for item in items}),
        by_owner=_dimension(inventories, "owner_team"),
        by_domain=_dimension(inventories, "business_domain"),
        by_risk=_dimension(inventories, "risk_level"),
        by_lifecycle=_dimension(inventories, "lifecycle"),
        most_executed=_top(executed, top_n),
        most_routed=_top(routed, top_n),
        unused_tools=[tid for tid in sorted({item.id for item in items}) if executed[tid] == 0],
        deprecated_in_use=sorted(
            {item.id for item in items if executed[item.id] > 0 and is_deprecated(item)}
        ),
        highest_latency=_rank(latency, "p95_ms", top_n),
        highest_failure_rate=_rank(failure_rate, "failure_rate", top_n),
        largest_schema=_rank(schema_sizes, "schema_chars", top_n),
        selected_not_executed=sorted(tid for tid in routed if executed[tid] == 0),
        collision_counts=dict(analyze_collisions(items).counts),
    )


def _section(lines: list[str], title: str, entries: list[str]) -> None:
    """Append a bulleted Markdown section (``- None`` when empty)."""
    lines.extend(["", f"### {title}", ""])
    if entries:
        lines.extend(f"- {entry}" for entry in entries)
    else:
        lines.append("- None")


def render_markdown(scorecard: Scorecard) -> str:
    """Render *scorecard* as deterministic Markdown, actionable findings first."""
    s = scorecard
    lines = ["# Tool-Surface Health Scorecard", "", "## Findings"]
    _section(lines, "Deprecated tools in use", [f"`{tid}`" for tid in s.deprecated_in_use])
    _section(
        lines,
        "Highest failure rate",
        [
            f"`{r['tool_id']}`: {r['failure_rate']:.2%} over {r['calls']} calls"
            for r in s.highest_failure_rate
        ],
    )
    _section(lines, "Unused tools (zero executions)", [f"`{tid}`" for tid in s.unused_tools])
    lines += [
        "",
        "## Totals",
        "",
        f"- Tools: {s.total_tools}",
        f"- Namespaces: {s.total_namespaces}",
    ]
    lines += ["", "## Inventory"]
    for title, counts in (
        ("By owner", s.by_owner),
        ("By domain", s.by_domain),
        ("By risk", s.by_risk),
        ("By lifecycle", s.by_lifecycle),
    ):
        _section(lines, title, [f"`{key}`: {count}" for key, count in counts.items()])
    lines += ["", "## Usage"]
    _section(lines, "Most executed", [f"`{r['tool_id']}`: {r['count']}" for r in s.most_executed])
    _section(lines, "Most routed", [f"`{r['tool_id']}`: {r['count']}" for r in s.most_routed])
    _section(
        lines,
        "Highest latency (p95)",
        [f"`{r['tool_id']}`: {r['p95_ms']}ms over {r['calls']} calls" for r in s.highest_latency],
    )
    _section(
        lines,
        "Largest schemas",
        [f"`{r['tool_id']}`: {r['schema_chars']} chars" for r in s.largest_schema],
    )
    _section(lines, "Selected but never executed", [f"`{tid}`" for tid in s.selected_not_executed])
    _section(lines, "Collisions", [f"`{kind}`: {n}" for kind, n in s.collision_counts.items()])
    return "\n".join(lines) + "\n"


def render_csv(scorecard: Scorecard) -> str:
    """Render *scorecard* as deterministic ``section,key,value`` CSV rows."""
    s = scorecard
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["section", "key", "value"])
    writer.writerow(["totals", "tools", s.total_tools])
    writer.writerow(["totals", "namespaces", s.total_namespaces])
    for section, counts in (
        ("by_owner", s.by_owner),
        ("by_domain", s.by_domain),
        ("by_risk", s.by_risk),
        ("by_lifecycle", s.by_lifecycle),
        ("collisions", s.collision_counts),
    ):
        writer.writerows([section, key, count] for key, count in counts.items())
    for section, rows, value_key in (
        ("most_executed", s.most_executed, "count"),
        ("most_routed", s.most_routed, "count"),
        ("highest_latency", s.highest_latency, "p95_ms"),
        ("highest_failure_rate", s.highest_failure_rate, "failure_rate"),
        ("largest_schema", s.largest_schema, "schema_chars"),
    ):
        writer.writerows([section, row["tool_id"], row[value_key]] for row in rows)
    for section, ids in (
        ("unused_tools", s.unused_tools),
        ("deprecated_in_use", s.deprecated_in_use),
        ("selected_not_executed", s.selected_not_executed),
    ):
        writer.writerows([section, tid, "true"] for tid in ids)
    return buffer.getvalue()


__all__ = [
    "SCORECARD_VERSION",
    "UNKNOWN",
    "Scorecard",
    "build_scorecard",
    "render_csv",
    "render_markdown",
]
