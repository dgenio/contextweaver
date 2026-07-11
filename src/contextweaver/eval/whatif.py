"""What-if operations simulator for catalog churn and traffic spikes (issue #662).

:func:`simulate` applies a :class:`ChurnScenario` to a copy of the catalog
(synthetic distractor tools added, a seeded sample of real tools removed or
renamed), measures top-*k* routing recall over caller-supplied probe queries
on both catalogs, and replays the scenario's traffic against a
:class:`~contextweaver.adapters.gateway_policy.RateLimitPolicy` under an
injected one-second-per-tick clock.  Deterministic by construction: all
randomness flows through one ``random.Random(seed)`` over sorted inputs, so
the same inputs and seed produce an identical :class:`WhatIfReport`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from typing import Any

from contextweaver.adapters.gateway_controls import RateLimiter
from contextweaver.adapters.gateway_policy import RateLimitPolicy
from contextweaver.exceptions import ConfigError
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import SelectableItem

__all__ = ["ChurnScenario", "WhatIfReport", "simulate"]

WHATIF_REPORT_VERSION: int = 1

# Rank cutoff for the recall measurement (mirrors ``eval.routing``'s top-5).
_TOP_K: int = 5


@dataclass
class ChurnScenario:
    """One hypothetical catalog-churn and traffic scenario.

    Attributes:
        name: Human-readable scenario label.
        add_tools: Synthetic distractor tools to add (namespace ``"synthetic"``).
        remove_tools: Existing tools to remove (seeded sample).
        rename_tools: Existing tools to rename — the ``tool_id`` changes, so
            probes expecting the old id miss (that is the signal).
        traffic_multiplier: Multiplier applied to the baseline requests/tick.
        duration_ticks: Simulated duration in one-second ticks.
    """

    name: str
    add_tools: int = 0
    remove_tools: int = 0
    rename_tools: int = 0
    traffic_multiplier: float = 1.0
    duration_ticks: int = 60

    def __post_init__(self) -> None:
        """Validate scenario bounds."""
        for label, value in (
            ("add_tools", self.add_tools),
            ("remove_tools", self.remove_tools),
            ("rename_tools", self.rename_tools),
        ):
            if value < 0:
                raise ConfigError(f"ChurnScenario.{label} must be >= 0")
        if self.traffic_multiplier < 0:
            raise ConfigError("ChurnScenario.traffic_multiplier must be >= 0")
        if self.duration_ticks < 1:
            raise ConfigError("ChurnScenario.duration_ticks must be >= 1")

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "name": self.name,
            "add_tools": self.add_tools,
            "remove_tools": self.remove_tools,
            "rename_tools": self.rename_tools,
            "traffic_multiplier": self.traffic_multiplier,
            "duration_ticks": self.duration_ticks,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChurnScenario:
        """Deserialise from a JSON-compatible dict."""
        return cls(
            name=str(data.get("name", "")),
            add_tools=int(data.get("add_tools", 0)),
            remove_tools=int(data.get("remove_tools", 0)),
            rename_tools=int(data.get("rename_tools", 0)),
            traffic_multiplier=float(data.get("traffic_multiplier", 1.0)),
            duration_ticks=int(data.get("duration_ticks", 60)),
        )


@dataclass
class WhatIfReport:
    """Measured outcome of one :func:`simulate` run.

    Attributes:
        scenario: The scenario name.
        catalog_size_before / catalog_size_after: Catalog sizes around churn.
        collisions_introduced: Net new duplicate ``(namespace, name)`` pairs
            the churn created (added or renamed tools colliding with survivors).
        routing_recall_before / routing_recall_after: Fraction of probes whose
            expected ``tool_id`` appeared in the top-5 candidates.
        shortlist_stability: Fraction of probes whose top-1 ``tool_id`` was
            unchanged after churn.
        rate_limit_breaches: Requests rejected by the rate-limit policy across
            the simulated traffic (``0`` when no policy was supplied).
        notes: Deterministic human-readable remarks (what was removed/renamed).
    """

    scenario: str
    catalog_size_before: int = 0
    catalog_size_after: int = 0
    collisions_introduced: int = 0
    routing_recall_before: float = 0.0
    routing_recall_after: float = 0.0
    shortlist_stability: float = 0.0
    rate_limit_breaches: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "version": WHATIF_REPORT_VERSION,
            "scenario": self.scenario,
            "catalog_size_before": self.catalog_size_before,
            "catalog_size_after": self.catalog_size_after,
            "collisions_introduced": self.collisions_introduced,
            "routing_recall_before": self.routing_recall_before,
            "routing_recall_after": self.routing_recall_after,
            "shortlist_stability": self.shortlist_stability,
            "rate_limit_breaches": self.rate_limit_breaches,
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WhatIfReport:
        """Deserialise from a JSON-compatible dict."""
        return cls(
            scenario=str(data.get("scenario", "")),
            catalog_size_before=int(data.get("catalog_size_before", 0)),
            catalog_size_after=int(data.get("catalog_size_after", 0)),
            collisions_introduced=int(data.get("collisions_introduced", 0)),
            routing_recall_before=float(data.get("routing_recall_before", 0.0)),
            routing_recall_after=float(data.get("routing_recall_after", 0.0)),
            shortlist_stability=float(data.get("shortlist_stability", 0.0)),
            rate_limit_breaches=int(data.get("rate_limit_breaches", 0)),
            notes=list(data.get("notes", [])),
        )

    def render_markdown(self) -> str:
        """Render the report as deterministic Markdown."""
        lines = [
            f"# What-If Report: {self.scenario}",
            "",
            f"- Catalog size: {self.catalog_size_before} -> {self.catalog_size_after}",
            f"- Collisions introduced: {self.collisions_introduced}",
            f"- Routing recall@{_TOP_K}: {self.routing_recall_before:.4f} -> "
            f"{self.routing_recall_after:.4f}",
            f"- Shortlist stability (top-1 unchanged): {self.shortlist_stability:.4f}",
            f"- Rate-limit breaches: {self.rate_limit_breaches}",
            "",
            "## Notes",
        ]
        if self.notes:
            lines.extend(f"- {note}" for note in self.notes)
        else:
            lines.append("- None")
        return "\n".join(lines) + "\n"


def _apply_churn(
    items: list[SelectableItem], scenario: ChurnScenario, rng: random.Random, notes: list[str]
) -> list[SelectableItem]:
    """Return the post-churn catalog; record removals/renames in *notes*."""
    survivors = sorted(items, key=lambda item: item.id)
    remove_n = min(scenario.remove_tools, max(len(survivors) - 1, 0))
    if remove_n < scenario.remove_tools:
        notes.append(f"remove_tools capped at {remove_n} to keep the catalog non-empty")
    removed_ids = {item.id for item in rng.sample(survivors, remove_n)} if remove_n else set()
    survivors = [item for item in survivors if item.id not in removed_ids]
    rename_n = min(scenario.rename_tools, len(survivors))
    renamed_ids = {item.id for item in rng.sample(survivors, rename_n)} if rename_n else set()
    churned = [
        replace(item, id=f"{item.id}.v2", name=item.name) if item.id in renamed_ids else item
        for item in survivors
    ]
    for i in range(scenario.add_tools):
        churned.append(
            SelectableItem(
                id=f"synthetic.tool_{i}",
                kind="tool",
                name=f"tool_{i}",
                namespace="synthetic",
                description=f"Synthetic distractor tool number {i} added by the what-if simulator.",
            )
        )
    if removed_ids:
        notes.append("removed: " + ", ".join(sorted(removed_ids)))
    if renamed_ids:
        notes.append("renamed: " + ", ".join(sorted(renamed_ids)))
    return sorted(churned, key=lambda item: item.id)


def _duplicate_names(items: list[SelectableItem]) -> int:
    """Count items sharing a ``(namespace, name)`` pair with an earlier item."""
    seen: set[tuple[str, str]] = set()
    duplicates = 0
    for item in sorted(items, key=lambda entry: entry.id):
        key = (item.namespace, item.name)
        duplicates += key in seen
        seen.add(key)
    return duplicates


def _probe(
    items: list[SelectableItem], probes: list[tuple[str, str]]
) -> tuple[dict[str, str], float]:
    """Route every probe; return per-query top-1 ids and recall@:data:`_TOP_K`."""
    graph = TreeBuilder(max_children=20).build(items)
    router = Router(graph, items=list(items), top_k=_TOP_K)
    top1: dict[str, str] = {}
    hits = 0
    for query, expected in probes:
        candidate_ids = router.route(query).candidate_ids
        top1[query] = candidate_ids[0] if candidate_ids else ""
        hits += expected in candidate_ids[:_TOP_K]
    return top1, round(hits / len(probes), 4)


def _count_breaches(
    scenario: ChurnScenario, rate_limit: RateLimitPolicy | None, requests_per_tick: int
) -> int:
    """Replay the scenario's traffic against *rate_limit*; count rejections."""
    if rate_limit is None or not rate_limit.enabled:
        return 0
    per_tick = int(round(scenario.traffic_multiplier * requests_per_tick))
    clock = [0.0]
    limiter = RateLimiter(rate_limit, clock=lambda: clock[0])
    breaches = 0
    for tick in range(scenario.duration_ticks):
        clock[0] = float(tick)
        for _ in range(per_tick):
            breaches += not limiter.check("tool_execute").allowed
    return breaches


def simulate(
    items: list[SelectableItem],
    scenario: ChurnScenario,
    probes: list[tuple[str, str]],
    *,
    seed: int = 0,
    rate_limit: RateLimitPolicy | None = None,
    requests_per_tick: int = 10,
) -> WhatIfReport:
    """Simulate *scenario* against *items* and measure the operational impact.

    Args:
        items: The current catalog.
        scenario: The churn/traffic scenario to apply.
        probes: ``(query, expected_tool_id)`` pairs used to measure recall and
            shortlist stability.  Expected ids removed or renamed by the churn
            count as misses — that recall drop is the signal.
        seed: Seed for the churn sample; same seed → identical report.
        rate_limit: Optional gateway rate-limit policy to replay traffic
            against (``tool_execute`` quota).
        requests_per_tick: Baseline requests per one-second tick, scaled by
            :attr:`ChurnScenario.traffic_multiplier`.

    Returns:
        A fully-populated :class:`WhatIfReport`.

    Raises:
        ConfigError: If *items* or *probes* is empty, or
            *requests_per_tick* is negative.
    """
    if not items:
        raise ConfigError("simulate() requires a non-empty catalog")
    if not probes:
        raise ConfigError("simulate() requires at least one probe query")
    if requests_per_tick < 0:
        raise ConfigError("requests_per_tick must be >= 0")
    notes: list[str] = []
    rng = random.Random(seed)
    churned = _apply_churn(items, scenario, rng, notes)
    top1_before, recall_before = _probe(items, probes)
    top1_after, recall_after = _probe(churned, probes)
    stable = sum(top1_before[query] == top1_after[query] for query, _ in probes)
    return WhatIfReport(
        scenario=scenario.name,
        catalog_size_before=len(items),
        catalog_size_after=len(churned),
        collisions_introduced=max(_duplicate_names(churned) - _duplicate_names(items), 0),
        routing_recall_before=recall_before,
        routing_recall_after=recall_after,
        shortlist_stability=round(stable / len(probes), 4),
        rate_limit_breaches=_count_breaches(scenario, rate_limit, requests_per_tick),
        notes=notes,
    )
