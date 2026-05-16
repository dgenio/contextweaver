#!/usr/bin/env python3
"""Weight-sweep tool for ``ScoringConfig`` (issue #214).

Grid-searches the five ``ScoringConfig`` weights against the committed
benchmark scenarios and emits ``benchmarks/sweep_scoring.md`` — a
deterministic report ranking every weight tuple by a documented composite
score. Surfaces Pareto-dominating configs (if any) as candidates for a
follow-up defaults-change PR; **never** modifies ``ScoringConfig`` defaults
in this script.

Search space (3⁵ = 243 cells, full grid by default):

    recency_weight       ∈ {0.20, 0.30, 0.40}
    tag_match_weight     ∈ {0.15, 0.25, 0.35}
    kind_priority_weight ∈ {0.25, 0.35, 0.45}
    token_cost_penalty   ∈ {0.05, 0.10, 0.15}
    dedup_threshold      ∈ {0.80, 0.85, 0.90}

Composite score (documented in the report header):

    composite = coverage_pct
               - 5.0 * over_budget_penalty
               - 0.5 * abs(dropped - default_dropped)

Where ``coverage_pct`` is the same proxy used by ``scripts/baseline_naive.py``
and ``over_budget_penalty`` = ``max(0, util% - 100) / 10``. The formula
rewards configs that surface more events into the final pack while penalising
ones that bust the budget. It deliberately does **not** reward raw
``included_count`` — that would favour kitchen-sink configs.

The default config is included in the sweep and its rank is reported
explicitly. Pareto-dominating configs (better on every metric vs. default
at the *same* dedup_threshold) are flagged as candidates for a follow-up
issue.

Usage::

    python scripts/sweep_scoring.py
    python scripts/sweep_scoring.py --output benchmarks/sweep_scoring.md
    python scripts/sweep_scoring.py --sample 81   # stratified sample
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from contextweaver.config import ContextBudget, ScoringConfig  # noqa: E402
from contextweaver.context.manager import ContextManager  # noqa: E402
from contextweaver.protocols import CharDivFourEstimator  # noqa: E402
from contextweaver.store.event_log import InMemoryEventLog  # noqa: E402
from contextweaver.types import ContextItem, ItemKind, Phase  # noqa: E402

_SCENARIOS_DIR = _ROOT / "benchmarks" / "scenarios"
_DEFAULT_OUTPUT = _ROOT / "benchmarks" / "sweep_scoring.md"
_BUDGET = ContextBudget(route=2000, call=4000, interpret=4000, answer=6000)
_ESTIMATOR = CharDivFourEstimator()

_KIND_MAP: dict[str, ItemKind] = {
    "user_turn": ItemKind.user_turn,
    "agent_msg": ItemKind.agent_msg,
    "tool_call": ItemKind.tool_call,
    "tool_result": ItemKind.tool_result,
}

# Search grid (sub-300-line module ceiling; widening the grid is the work of
# a future issue when more time on the runner is acceptable).
_GRID: dict[str, tuple[float, ...]] = {
    "recency_weight": (0.20, 0.30, 0.40),
    "tag_match_weight": (0.15, 0.25, 0.35),
    "kind_priority_weight": (0.25, 0.35, 0.45),
    "token_cost_penalty": (0.05, 0.10, 0.15),
    "dedup_threshold": (0.80, 0.85, 0.90),
}


# ---------------------------------------------------------------------------
# Scenario loading
# ---------------------------------------------------------------------------


def _load_scenario(path: Path) -> list[ContextItem]:
    items: list[ContextItem] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        raw = json.loads(line)
        kind = _KIND_MAP.get(raw.get("type", ""), ItemKind.user_turn)
        items.append(
            ContextItem(
                id=raw["id"],
                kind=kind,
                text=raw.get("text", ""),
                parent_id=raw.get("parent_id"),
            )
        )
    return items


# ---------------------------------------------------------------------------
# Sweep cell evaluation
# ---------------------------------------------------------------------------


@dataclass
class SweepRow:
    config: dict[str, float]
    included: int
    dropped: int
    dedup: int
    tokens: int
    util_pct: float
    coverage_pct: float
    composite: float


def _evaluate_cell(
    scenario_paths: list[Path],
    cfg: ScoringConfig,
    default_dropped: int = 0,
) -> SweepRow:
    """Run all scenarios with *cfg* and return the aggregate row."""
    included = dropped = dedup = tokens = total_events = 0
    util_pcts: list[float] = []
    for path in scenario_paths:
        events = _load_scenario(path)
        total_events += len(events)
        log = InMemoryEventLog()
        for ev in events:
            log.append(ev)
        mgr = ContextManager(
            event_log=log,
            budget=_BUDGET,
            estimator=_ESTIMATOR,
            scoring_config=cfg,
        )
        query = next((ev.text for ev in reversed(events) if ev.kind == ItemKind.user_turn), "")
        pack = mgr.build_sync(phase=Phase.answer, query=query)
        included += pack.stats.included_count
        dropped += pack.stats.dropped_count
        dedup += pack.stats.dedup_removed
        tokens += _ESTIMATOR.estimate(pack.prompt)
        util_pcts.append((_ESTIMATOR.estimate(pack.prompt) / _BUDGET.for_phase(Phase.answer)) * 100)

    coverage_pct = round(included / max(total_events, 1) * 100, 2)
    avg_util = sum(util_pcts) / len(util_pcts) if util_pcts else 0.0
    over_budget = max(0.0, avg_util - 100.0) / 10.0
    composite = round(
        coverage_pct - 5.0 * over_budget - 0.5 * abs(dropped - default_dropped),
        4,
    )
    return SweepRow(
        config={
            "recency_weight": cfg.recency_weight,
            "tag_match_weight": cfg.tag_match_weight,
            "kind_priority_weight": cfg.kind_priority_weight,
            "token_cost_penalty": cfg.token_cost_penalty,
            "dedup_threshold": cfg.dedup_threshold,
        },
        included=included,
        dropped=dropped,
        dedup=dedup,
        tokens=tokens,
        util_pct=round(avg_util, 2),
        coverage_pct=coverage_pct,
        composite=composite,
    )


def _enumerate_grid(sample: int | None) -> list[ScoringConfig]:
    """Enumerate the search grid; stratified sample when ``sample`` is given."""
    keys = list(_GRID.keys())
    values = [_GRID[k] for k in keys]
    configs: list[ScoringConfig] = []
    for combo in itertools.product(*values):
        kwargs = dict(zip(keys, combo, strict=True))
        configs.append(ScoringConfig(**kwargs))
    if sample is None or sample >= len(configs):
        return configs
    # Deterministic stratified sample — take every (n // sample)-th entry.
    step = max(1, len(configs) // sample)
    return configs[::step][:sample]


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


_HEADER = """# contextweaver — ScoringConfig Weight Sweep

> Auto-generated by `make sweep-scoring`. Do not edit by hand.
> Source: ``benchmarks/scenarios/*.jsonl`` + ``ScoringConfig`` grid in
> ``scripts/sweep_scoring.py``.

Composite formula:

```
composite = coverage_pct
          - 5.0 * max(0, avg_util_pct - 100) / 10
          - 0.5 * abs(dropped - default_dropped)
```

* ``coverage_pct`` = ``included / event_count × 100`` aggregated across
  scenarios — the same coverage proxy used by ``scripts/baseline_naive.py``.
* The mid-term penalises configs that bust the answer-phase budget.
* The last term penalises configs that drop substantially more (or fewer)
  items than the default, so the composite favours configs *close* to the
  default's drop profile but with higher coverage.

The default ``ScoringConfig`` is included in the sweep so its rank is
visible alongside the alternatives. **This script does NOT change
``ScoringConfig`` defaults** — Pareto-dominating configs (if any) are
flagged below as candidates for a deliberately-scoped follow-up.
"""


def _row_line(rank: int, r: SweepRow, default_id: str) -> str:
    cfg = r.config
    is_default = (
        "**default**"
        if (
            cfg["recency_weight"] == 0.30
            and cfg["tag_match_weight"] == 0.25
            and cfg["kind_priority_weight"] == 0.35
            and cfg["token_cost_penalty"] == 0.10
            and cfg["dedup_threshold"] == 0.85
        )
        else ""
    )
    return (
        f"| {rank} | {cfg['recency_weight']:.2f} | "
        f"{cfg['tag_match_weight']:.2f} | {cfg['kind_priority_weight']:.2f} "
        f"| {cfg['token_cost_penalty']:.2f} | {cfg['dedup_threshold']:.2f} "
        f"| {r.included} | {r.dropped} | {r.dedup} | {r.tokens} "
        f"| {r.util_pct:.2f}% | {r.coverage_pct:.2f}% | {r.composite:.4f} "
        f"| {is_default} |"
    )


def render_report(rows: list[SweepRow]) -> str:
    """Render the sweep report markdown (deterministic for fixed inputs)."""
    # Deterministic ordering: by composite DESC, then by tuple-of-weights ASC
    # so ties resolve to the same row order on every run.
    rows_sorted = sorted(
        rows,
        key=lambda r: (
            -r.composite,
            r.config["recency_weight"],
            r.config["tag_match_weight"],
            r.config["kind_priority_weight"],
            r.config["token_cost_penalty"],
            r.config["dedup_threshold"],
        ),
    )
    top_n = 10
    parts: list[str] = [_HEADER, ""]

    # Default rank
    default_index = next(
        (
            i
            for i, r in enumerate(rows_sorted, 1)
            if r.config
            == {
                "recency_weight": 0.30,
                "tag_match_weight": 0.25,
                "kind_priority_weight": 0.35,
                "token_cost_penalty": 0.10,
                "dedup_threshold": 0.85,
            }
        ),
        None,
    )
    if default_index is not None:
        parts.append(f"Default `ScoringConfig` ranks **#{default_index}** of {len(rows_sorted)}.")
    parts.append("")

    parts.extend(
        [
            f"## Top {top_n} configs",
            "",
            "| rank | rec | tag | kind | cost | dedup | incl | drop | "
            "dedup_n | tokens | util% | cov% | composite | note |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for i, r in enumerate(rows_sorted[:top_n], 1):
        parts.append(_row_line(i, r, default_id="default"))
    parts.append("")

    # Pareto candidates: configs that strictly dominate the default
    if default_index is not None:
        default_row = rows_sorted[default_index - 1]
        pareto_candidates = [
            r
            for r in rows_sorted
            if r is not default_row
            and r.coverage_pct >= default_row.coverage_pct
            and r.dropped <= default_row.dropped
            and r.tokens <= default_row.tokens
            and (
                r.coverage_pct > default_row.coverage_pct
                or r.dropped < default_row.dropped
                or r.tokens < default_row.tokens
            )
        ]
        parts.extend(
            [
                "## Candidates for a follow-up defaults change",
                "",
                "Configs that weakly dominate the default on ``(coverage_pct,",
                "dropped, tokens)`` and strictly improve on at least one of the",
                "three. **Not actionable in this PR** — defaults change is a",
                "separate deliberately-scoped follow-up.",
                "",
            ]
        )
        if pareto_candidates:
            parts.extend(
                [
                    "| rec | tag | kind | cost | dedup | cov% | drop | tokens |",
                    "|---:|---:|---:|---:|---:|---:|---:|---:|",
                ]
            )
            for r in pareto_candidates[:5]:
                cfg = r.config
                parts.append(
                    f"| {cfg['recency_weight']:.2f} | {cfg['tag_match_weight']:.2f} "
                    f"| {cfg['kind_priority_weight']:.2f} "
                    f"| {cfg['token_cost_penalty']:.2f} | {cfg['dedup_threshold']:.2f} "
                    f"| {r.coverage_pct:.2f}% | {r.dropped} | {r.tokens} |"
                )
        else:
            parts.append("_No Pareto-dominating config found in the current grid._")
        parts.append("")

    parts.extend(
        [
            "## Reproducing",
            "",
            "```bash",
            "make sweep-scoring",
            "git diff --quiet benchmarks/sweep_scoring.md  # passes on clean re-run",
            "```",
            "",
        ]
    )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--output", default=str(_DEFAULT_OUTPUT), help="Path to write the sweep report."
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help="If set, stratified-sample this many configs from the full grid.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    scenario_paths = sorted(_SCENARIOS_DIR.glob("*.jsonl"))
    if not scenario_paths:
        print("No scenarios found.", file=sys.stderr)
        return 1

    # First pass: evaluate the default to obtain its drop count (anchor for
    # the composite's third term).
    default_cfg = ScoringConfig()
    default_row = _evaluate_cell(scenario_paths, default_cfg, default_dropped=0)
    default_dropped = default_row.dropped

    rows: list[SweepRow] = [default_row]
    for cfg in _enumerate_grid(args.sample):
        # Skip re-evaluating the default
        if (
            cfg.recency_weight == default_cfg.recency_weight
            and cfg.tag_match_weight == default_cfg.tag_match_weight
            and cfg.kind_priority_weight == default_cfg.kind_priority_weight
            and cfg.token_cost_penalty == default_cfg.token_cost_penalty
            and cfg.dedup_threshold == default_cfg.dedup_threshold
        ):
            continue
        rows.append(_evaluate_cell(scenario_paths, cfg, default_dropped=default_dropped))

    report = render_report(rows)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(f"Evaluated {len(rows)} configs; wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
