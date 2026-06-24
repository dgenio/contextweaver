"""Scenario benchmark: naive all-tools prompt vs bounded ChoiceCard routing (#418).

A scenario-style benchmark that makes contextweaver's routing value concrete:
for each tool-heavy task it contrasts the two prompt-construction strategies a
tool-using agent can pick from —

1. **naive** — expose *every* tool's name + description to the model;
2. **contextweaver** — route the query and expose only the bounded ``ChoiceCard``
   shortlist.

For each scenario it reports whether the expected tool stays reachable
(correct-in-top-k + its rank), how many cards are shown, and the prompt-token
cost of each strategy. Deterministic and offline: catalogs are seeded and token
counts use ``CharDivFourEstimator``, so the report is environment-independent.

It does not depend on LangWatch (the inspiration) or any hosted workspace, and
reuses only the installed package, mirroring ``benchmarks/smoke_eval.py``.

Usage::

    python benchmarks/scenario_routing.py            # write the markdown report
    python benchmarks/scenario_routing.py --check     # exit non-zero on drift

Exit codes: 0 on success; 1 on report drift (``--check``).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from contextweaver.protocols import CharDivFourEstimator  # noqa: E402
from contextweaver.routing.cards import make_choice_cards, render_cards_text  # noqa: E402
from contextweaver.routing.catalog import (  # noqa: E402
    generate_sample_catalog,
    load_catalog_dicts,
)
from contextweaver.routing.router import Router  # noqa: E402
from contextweaver.routing.tree import TreeBuilder  # noqa: E402
from contextweaver.types import SelectableItem  # noqa: E402

DEFAULT_DATASET = _ROOT / "benchmarks" / "scenarios" / "routing_choicecard.json"
DEFAULT_OUTPUT = _ROOT / "benchmarks" / "scenario_routing.md"
SEED = 42
TOP_K = 5
BEAM_WIDTH = 3

_EST = CharDivFourEstimator()


def _count(text: str) -> int:
    return _EST.estimate(text)


def _make_catalog(n: int, seed: int = SEED) -> list[SelectableItem]:
    """Deterministic catalog of *n* tools, extending the 83-item pool with variants."""
    base = load_catalog_dicts(generate_sample_catalog(n=83, seed=seed))
    items: list[SelectableItem] = list(base)
    version = 2
    while len(items) < n:
        for orig in list(base):
            items.append(
                SelectableItem(
                    f"{orig.id}.v{version}",
                    orig.kind,
                    f"{orig.name}_v{version}",
                    f"{orig.description} (variant {version})",
                    tags=orig.tags,
                    namespace=orig.namespace,
                )
            )
            if len(items) >= n:
                break
        version += 1
    return sorted(items, key=lambda i: i.id)[:n]


@dataclass
class ScenarioRow:
    """One scenario's naive-vs-ChoiceCard comparison."""

    name: str
    catalog_size: int
    correct_in_top_k: bool
    correct_rank: int  # 1-based; 0 = not in shortlist
    cards_shown: int
    naive_tokens: int
    card_tokens: int
    token_reduction_pct: float


def run_scenario(scenario: dict[str, object]) -> ScenarioRow:
    """Route one scenario and return its comparison row."""
    size = int(scenario["catalog_size"])  # type: ignore[arg-type]
    query = str(scenario["query"])
    expected = set(scenario.get("expected", []))  # type: ignore[arg-type]
    items = _make_catalog(size)
    router = Router(TreeBuilder().build(items), items=items, top_k=TOP_K, beam_width=BEAM_WIDTH)
    result = router.route(query)

    candidate_ids = list(result.candidate_ids)
    rank = next((i + 1 for i, cid in enumerate(candidate_ids) if cid in expected), 0)
    cards = make_choice_cards(result.candidate_items)
    naive_tokens = _count("\n".join(f"{it.name}: {it.description}" for it in items))
    card_tokens = _count(render_cards_text(cards))
    reduction = round((1 - card_tokens / naive_tokens) * 100.0, 2) if naive_tokens else 0.0
    return ScenarioRow(
        name=str(scenario["name"]),
        catalog_size=len(items),
        correct_in_top_k=rank > 0,
        correct_rank=rank,
        cards_shown=len(cards),
        naive_tokens=naive_tokens,
        card_tokens=card_tokens,
        token_reduction_pct=reduction,
    )


def run_all(dataset_path: Path = DEFAULT_DATASET) -> list[ScenarioRow]:
    """Run every scenario in *dataset_path*, ordered by scenario name."""
    scenarios = json.loads(dataset_path.read_text(encoding="utf-8"))
    rows = [run_scenario(s) for s in scenarios]
    return sorted(rows, key=lambda r: r.name)


def render_report(rows: list[ScenarioRow]) -> str:
    """Render the deterministic scenario comparison report."""
    hits = sum(1 for r in rows if r.correct_in_top_k)
    mean_reduction = round(sum(r.token_reduction_pct for r in rows) / len(rows), 2) if rows else 0.0
    lines = [
        "# contextweaver — Scenario Routing Benchmark",
        "",
        "> Auto-generated by `make benchmark-scenario`. Do not edit by hand.",
        "> Source: `benchmarks/scenario_routing.py` (issue #418). Offline, deterministic.",
        "",
        "Naive all-tools prompting vs bounded `ChoiceCard` routing across tool-heavy",
        "scenarios. Token counts use `CharDivFourEstimator` (no model dependency).",
        "",
        f"- Scenarios: `{len(rows)}` · correct tool in top-{TOP_K}: "
        f"`{hits}/{len(rows)}` · mean token reduction: `{mean_reduction:.2f}%`",
        "",
        "| scenario | catalog | correct@top-k | rank | cards | naive tokens "
        "| card tokens | reduction |",
        "|---|---:|:---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        correct = "✅" if r.correct_in_top_k else "❌"
        rank = str(r.correct_rank) if r.correct_rank else "—"
        lines.append(
            f"| {r.name} | {r.catalog_size} | {correct} | {rank} | {r.cards_shown} "
            f"| {r.naive_tokens} | {r.card_tokens} | {r.token_reduction_pct:.2f}% |"
        )
    lines.extend(
        [
            "",
            "Reading the table:",
            "",
            "- `correct@top-k` is whether the expected tool survived into the bounded",
            "  shortlist — the property naive prompting trivially satisfies (every tool",
            "  is present) but at the token cost in the `naive tokens` column.",
            "- `reduction` is how much smaller the ChoiceCard prompt is than listing",
            "  every tool's name + description — the headline routing benefit at scale.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--check", action="store_true", help="Exit non-zero on report drift.")
    args = parser.parse_args(argv)

    report = render_report(run_all(Path(args.dataset)))
    output = Path(args.output)
    if args.check:
        current = output.read_text(encoding="utf-8") if output.exists() else ""
        if current != report:
            print(
                "scenario report drift — run `make benchmark-scenario` and commit.",
                file=sys.stderr,
            )
            return 1
        print("scenario report: up to date")
        return 0
    output.write_text(report, encoding="utf-8", newline="\n")
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
