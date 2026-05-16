#!/usr/bin/env python3
"""ScoringConfig weight sweep + measured-defaults report (issue #214).

Grid-searches the ``ScoringConfig`` weight space against the committed
benchmark scenarios and writes a deterministic markdown report to
``benchmarks/sweep_scoring.md`` showing:

* The composite score and per-axis metrics for every configuration.
* The rank of the current default within the swept grid.
* Any configurations that Pareto-dominate the default (lower or equal on
  every axis we care about, strictly better on at least one) — surfaced
  as candidates for a separate, deliberately-scoped follow-up issue.

Per the issue spec, this script does **not** change ``ScoringConfig``
defaults. The report is the deliverable; defaults remain the
maintainer's call.

Composite formula (also documented in the report header)::

    composite = 0.50 * coverage_pct_avg
              + 0.30 * (100 - util_overrun_avg)
              + 0.20 * (100 - drop_rate_avg)

* ``coverage_pct_avg`` — average naïve-baseline coverage_pct across
  scenarios (higher = more parent chains preserved).
* ``util_overrun_avg`` — average over-budget pressure across scenarios,
  ``max(0, util_pct - 100)`` (lower = better).
* ``drop_rate_avg`` — average ``items_dropped / max(1, event_count)``
  expressed as a percent (lower = better).

The composite deliberately weights coverage above raw inclusion count so
that "kitchen-sink" configs (which inflate ``included_count`` by
accepting more low-quality items) do not win.

Usage::

    python scripts/sweep_scoring.py
    python scripts/sweep_scoring.py --output benchmarks/sweep_scoring.md
"""

from __future__ import annotations

import argparse
import itertools
import json
import statistics
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))

from baseline_naive import compute_naive_delta  # noqa: E402

from contextweaver.config import ContextBudget, ScoringConfig  # noqa: E402
from contextweaver.context.manager import ContextManager  # noqa: E402
from contextweaver.protocols import CharDivFourEstimator  # noqa: E402
from contextweaver.store.event_log import InMemoryEventLog  # noqa: E402
from contextweaver.types import ContextItem, ItemKind, Phase  # noqa: E402

# ---------------------------------------------------------------------------
# Sweep grid (per #214 issue body)
# ---------------------------------------------------------------------------

_RECENCY_WEIGHTS: tuple[float, ...] = (0.2, 0.3, 0.4)
_TAG_MATCH_WEIGHTS: tuple[float, ...] = (0.15, 0.25, 0.35)
_KIND_PRIORITY_WEIGHTS: tuple[float, ...] = (0.25, 0.35, 0.45)
_TOKEN_COST_PENALTIES: tuple[float, ...] = (0.05, 0.10, 0.15)
_DEDUP_THRESHOLDS: tuple[float, ...] = (0.80, 0.85, 0.90)

_BENCH_DIR = _ROOT / "benchmarks"
_SCENARIOS_DIR = _BENCH_DIR / "scenarios"
_DEFAULT_OUTPUT = _BENCH_DIR / "sweep_scoring.md"

_BUDGET = ContextBudget(route=2000, call=4000, interpret=4000, answer=6000)
_ESTIMATOR = CharDivFourEstimator()

_KIND_MAP: dict[str, ItemKind] = {
    "user_turn": ItemKind.user_turn,
    "agent_msg": ItemKind.agent_msg,
    "tool_call": ItemKind.tool_call,
    "tool_result": ItemKind.tool_result,
}


@dataclass(frozen=True)
class WeightTuple:
    """One point in the 5-D ScoringConfig grid."""

    recency_weight: float
    tag_match_weight: float
    kind_priority_weight: float
    token_cost_penalty: float
    dedup_threshold: float

    def as_config(self) -> ScoringConfig:
        return ScoringConfig(
            recency_weight=self.recency_weight,
            tag_match_weight=self.tag_match_weight,
            kind_priority_weight=self.kind_priority_weight,
            token_cost_penalty=self.token_cost_penalty,
            dedup_threshold=self.dedup_threshold,
        )

    def label(self) -> str:
        return (
            f"r={self.recency_weight:.2f} "
            f"tg={self.tag_match_weight:.2f} "
            f"kp={self.kind_priority_weight:.2f} "
            f"tc={self.token_cost_penalty:.2f} "
            f"dd={self.dedup_threshold:.2f}"
        )


@dataclass
class SweepRow:
    """One row of the sweep report."""

    tuple_: WeightTuple
    coverage_pct_avg: float
    util_overrun_avg: float
    drop_rate_avg: float
    composite: float

    @property
    def is_default(self) -> bool:
        default = ScoringConfig()
        return (
            self.tuple_.recency_weight == default.recency_weight
            and self.tuple_.tag_match_weight == default.tag_match_weight
            and self.tuple_.kind_priority_weight == default.kind_priority_weight
            and self.tuple_.token_cost_penalty == default.token_cost_penalty
            and self.tuple_.dedup_threshold == default.dedup_threshold
        )


def _load_scenarios() -> dict[str, list[ContextItem]]:
    """Eager-load all scenario JSONL files once (avoids re-parsing in the inner loop)."""
    out: dict[str, list[ContextItem]] = {}
    for path in sorted(_SCENARIOS_DIR.glob("*.jsonl")):
        events: list[ContextItem] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            events.append(
                ContextItem(
                    id=raw["id"],
                    kind=_KIND_MAP.get(raw.get("type", ""), ItemKind.user_turn),
                    text=raw.get("text", ""),
                    parent_id=raw.get("parent_id"),
                )
            )
        out[path.stem] = events
    return out


def _evaluate(
    config: ScoringConfig, scenarios: dict[str, list[ContextItem]]
) -> tuple[float, float, float]:
    """Run *config* on every scenario; return ``(coverage, util_overrun, drop_rate)`` averages."""
    coverages: list[float] = []
    util_overruns: list[float] = []
    drop_rates: list[float] = []
    for _name, events in scenarios.items():
        log = InMemoryEventLog()
        for ev in events:
            log.append(ev)
        mgr = ContextManager(
            event_log=log, budget=_BUDGET, estimator=_ESTIMATOR, scoring_config=config
        )
        query = next((ev.text for ev in reversed(events) if ev.kind == ItemKind.user_turn), "")
        pack = mgr.build_sync(phase=Phase.answer, query=query)

        prompt_toks = _ESTIMATOR.estimate(pack.prompt)
        budget_toks = _BUDGET.for_phase(Phase.answer)
        util_pct = prompt_toks / budget_toks * 100 if budget_toks else 0.0
        util_overruns.append(max(0.0, util_pct - 100))

        drop_rate = pack.stats.dropped_count / max(1, len(events)) * 100
        drop_rates.append(drop_rate)

        delta = compute_naive_delta(events=events, pack=pack, cw_tokens=prompt_toks)
        coverages.append(delta.coverage_pct)
    return (
        statistics.mean(coverages),
        statistics.mean(util_overruns),
        statistics.mean(drop_rates),
    )


def _composite(coverage: float, util_overrun: float, drop_rate: float) -> float:
    return 0.50 * coverage + 0.30 * (100.0 - util_overrun) + 0.20 * (100.0 - drop_rate)


def run_sweep(scenarios: dict[str, list[ContextItem]]) -> list[SweepRow]:
    """Evaluate every weight tuple; return rows sorted by composite descending."""
    tuples = [
        WeightTuple(r, tg, kp, tc, dd)
        for r, tg, kp, tc, dd in itertools.product(
            _RECENCY_WEIGHTS,
            _TAG_MATCH_WEIGHTS,
            _KIND_PRIORITY_WEIGHTS,
            _TOKEN_COST_PENALTIES,
            _DEDUP_THRESHOLDS,
        )
    ]
    rows: list[SweepRow] = []
    for tup in tuples:
        coverage, util_overrun, drop_rate = _evaluate(tup.as_config(), scenarios)
        rows.append(
            SweepRow(
                tuple_=tup,
                coverage_pct_avg=round(coverage, 4),
                util_overrun_avg=round(util_overrun, 4),
                drop_rate_avg=round(drop_rate, 4),
                composite=round(_composite(coverage, util_overrun, drop_rate), 4),
            )
        )
    # Sort by composite desc, then tuple lexicographic for deterministic ties.
    rows.sort(
        key=lambda r: (
            -r.composite,
            r.tuple_.recency_weight,
            r.tuple_.tag_match_weight,
            r.tuple_.kind_priority_weight,
            r.tuple_.token_cost_penalty,
            r.tuple_.dedup_threshold,
        )
    )
    return rows


def _pareto_dominators(default_row: SweepRow, rows: list[SweepRow]) -> list[SweepRow]:
    """Return rows that Pareto-dominate *default_row*.

    Higher is better for coverage; lower is better for util_overrun and
    drop_rate. Domination: better-or-equal on every axis, strictly better
    on at least one.
    """
    out: list[SweepRow] = []
    for r in rows:
        if r is default_row:
            continue
        be = (
            r.coverage_pct_avg >= default_row.coverage_pct_avg
            and r.util_overrun_avg <= default_row.util_overrun_avg
            and r.drop_rate_avg <= default_row.drop_rate_avg
        )
        strict = (
            r.coverage_pct_avg > default_row.coverage_pct_avg
            or r.util_overrun_avg < default_row.util_overrun_avg
            or r.drop_rate_avg < default_row.drop_rate_avg
        )
        if be and strict:
            out.append(r)
    return out


def render_report(rows: list[SweepRow]) -> str:
    """Render the sweep report markdown deterministically."""
    default_row = next((r for r in rows if r.is_default), None)
    default_rank = (rows.index(default_row) + 1) if default_row is not None else 0
    dominators = _pareto_dominators(default_row, rows) if default_row else []

    parts = [
        "# contextweaver — ScoringConfig sweep report",
        "",
        "> Auto-generated by `make sweep-scoring`. Do not edit by hand.",
        "> Source: `benchmarks/scenarios/*.jsonl` + the grid in",
        "> `scripts/sweep_scoring.py`. This report is **measurement**, not a",
        "> defaults change — per issue #214, defaults stay where they are",
        "> until a separate, deliberately-scoped follow-up issue ships them.",
        "",
        f"- Grid size: `{len(rows)}` configurations (3⁵ = 243)",
        f"- Scenarios evaluated: `{len(list(_SCENARIOS_DIR.glob('*.jsonl')))}` "
        "(every JSONL under `benchmarks/scenarios/`)",
        f"- Current default rank: `{default_rank}` / `{len(rows)}`",
        "",
        "## Composite formula",
        "",
        "```",
        "composite = 0.50 * coverage_pct_avg",
        "          + 0.30 * (100 - util_overrun_avg)",
        "          + 0.20 * (100 - drop_rate_avg)",
        "```",
        "",
        "- `coverage_pct_avg` — average parent-chain coverage across",
        "  scenarios (higher = more dependencies preserved).",
        "- `util_overrun_avg` — average over-budget pressure, "
        "`max(0, util% - 100)` (lower = better).",
        "- `drop_rate_avg` — average `items_dropped / event_count` as a",
        "  percent (lower = better).",
        "",
        "Coverage is weighted above raw count metrics so kitchen-sink configs",
        "(which inflate inclusions by accepting more low-quality items) do",
        "not win the ranking.",
        "",
        "## Top-10 configurations",
        "",
        "| rank | recency | tag | kind | token_cost | dedup | "
        "coverage % | util overrun % | drop rate % | composite | note |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for i, r in enumerate(rows[:10], start=1):
        note = "**default**" if r.is_default else ""
        parts.append(
            f"| {i} | {r.tuple_.recency_weight:.2f} "
            f"| {r.tuple_.tag_match_weight:.2f} "
            f"| {r.tuple_.kind_priority_weight:.2f} "
            f"| {r.tuple_.token_cost_penalty:.2f} "
            f"| {r.tuple_.dedup_threshold:.2f} "
            f"| {r.coverage_pct_avg:.2f} "
            f"| {r.util_overrun_avg:.2f} "
            f"| {r.drop_rate_avg:.2f} "
            f"| {r.composite:.2f} "
            f"| {note} |"
        )

    if default_row is not None and default_rank > 10:
        parts += [
            "",
            f"### Default (rank {default_rank})",
            "",
            f"`{default_row.tuple_.label()}` — coverage "
            f"{default_row.coverage_pct_avg:.2f}%, util_overrun "
            f"{default_row.util_overrun_avg:.2f}%, drop_rate "
            f"{default_row.drop_rate_avg:.2f}%, composite "
            f"{default_row.composite:.2f}.",
        ]

    parts += ["", "## Pareto-dominating configurations", ""]
    if not dominators:
        parts.append(
            "_None._ No grid point Pareto-dominates the current default on "
            "(coverage, util_overrun, drop_rate). The default is on the "
            "Pareto frontier for this scenario set."
        )
    else:
        parts += [
            f"{len(dominators)} grid point(s) Pareto-dominate the current default. "
            "Surfaced here as **candidates for a follow-up issue**, not as a "
            "defaults change in this PR (per #214 non-goals).",
            "",
            "| recency | tag | kind | token_cost | dedup | "
            "coverage % | util overrun % | drop rate % | composite |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for r in dominators:
            parts.append(
                f"| {r.tuple_.recency_weight:.2f} "
                f"| {r.tuple_.tag_match_weight:.2f} "
                f"| {r.tuple_.kind_priority_weight:.2f} "
                f"| {r.tuple_.token_cost_penalty:.2f} "
                f"| {r.tuple_.dedup_threshold:.2f} "
                f"| {r.coverage_pct_avg:.2f} "
                f"| {r.util_overrun_avg:.2f} "
                f"| {r.drop_rate_avg:.2f} "
                f"| {r.composite:.2f} |"
            )

    parts += [
        "",
        "## Regenerating",
        "",
        "```bash",
        "make sweep-scoring",
        "git diff --quiet benchmarks/sweep_scoring.md   # passes on clean re-run",
        "```",
        "",
    ]
    return "\n".join(parts)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else None,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--output",
        default=str(_DEFAULT_OUTPUT),
        help="Path to write the sweep report",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    scenarios = _load_scenarios()
    if not scenarios:
        print("No scenarios found under benchmarks/scenarios/", file=sys.stderr)
        return 1
    rows = run_sweep(scenarios)
    text = render_report(rows)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    print(f"Wrote {out_path} ({len(rows)} configurations)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
