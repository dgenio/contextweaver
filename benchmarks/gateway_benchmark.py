"""Gateway-scenario benchmark suite (issue #270).

Runs the MCP Context Gateway architecture's catalog + firewall pipeline
across a small but varied set of scenarios so the marketing-shaped "98.8 %
single-call firewall reduction" claim becomes a **measured range** rather
than a single anecdote.

What it measures (per scenario)::

    catalog_tools          — same 60-tool gateway catalog for every scenario
    exposed_choice_cards   — len(shortlist) at top_k=5
    raw_result_chars       — bytes of the mocked upstream response
    injected_summary_chars — bytes that actually land in the answer prompt
    firewall_reduction_pct — 1 - summary / raw
    final_prompt_tokens    — answer-phase prompt token count
    firewall_triggered     — True iff raw exceeded the 2000-char threshold

Scenarios are deterministic, network-free, and emit a sorted-key JSON file
so ``--check`` mode is byte-stable across machines.

Usage::

    python benchmarks/gateway_benchmark.py
    python benchmarks/gateway_benchmark.py --output benchmarks/results/gateway_latest.json
    python benchmarks/gateway_benchmark.py --check

Exit codes: 0 on success, 1 on any error. ``--check`` exits 1 when the
on-disk output drifts from a freshly-generated run.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from contextweaver.config import ContextBudget  # noqa: E402
from contextweaver.context.manager import ContextManager  # noqa: E402
from contextweaver.data import gateway_catalog_path  # noqa: E402
from contextweaver.routing.cards import make_choice_cards  # noqa: E402
from contextweaver.routing.catalog import Catalog, load_catalog_yaml  # noqa: E402
from contextweaver.routing.router import Router  # noqa: E402
from contextweaver.routing.tree import TreeBuilder  # noqa: E402
from contextweaver.types import ContextItem, ItemKind, Phase  # noqa: E402

FIREWALL_THRESHOLD = 2000
BENCHMARK_VERSION = "1.0"


@dataclass
class GatewayScenario:
    """One benchmarked gateway scenario.

    Fields are deliberately small so the JSON output stays diff-friendly.
    """

    name: str
    routing_query: str
    intent: str
    upstream_text: str
    user_query: str


@dataclass
class GatewayScenarioResult:
    """Per-scenario metrics emitted to ``gateway_latest.json``."""

    name: str
    catalog_tools: int
    exposed_choice_cards: int
    raw_result_chars: int
    injected_summary_chars: int
    firewall_reduction_pct: float
    firewall_triggered: bool
    final_prompt_tokens: int
    selected_tool_id: str
    chosen_was_intent: bool


def _tiny_payload() -> str:
    """Below the firewall threshold — proves the firewall correctly no-ops."""
    return "status: ok\nrows_returned: 2\n\ninvoice INV-001 paid\ninvoice INV-002 paid\n"


def _medium_payload() -> str:
    """~5 KB CRM-style payload."""
    rows = []
    for i in range(1, 41):
        rows.append(
            json.dumps(
                {
                    "account_id": f"C-{i:05d}",
                    "name": f"Acme Subsidiary {i}",
                    "tier": "growth" if i % 3 else "enterprise",
                    "owner": "self-serve" if i % 5 else "csm",
                    "mrr": (i * 137) % 5000,
                    "notes": f"daily reconcile row {i}",
                }
            )
        )
    return "accounts_returned: 40\n\n" + "\n".join(rows) + "\n"


def _large_payload() -> str:
    """~16 KB BigQuery-style rowset (the canonical scenario from main.py)."""
    rows = []
    for day in range(1, 91):
        delta = -450 if day == 47 else (137 * day) % 600 - 300
        rows.append(
            {
                "date": f"2026-{(day - 1) // 30 + 2:02d}-{((day - 1) % 30) + 1:02d}",
                "customer_id": "C-12345",
                "plan": "growth" if day < 47 else "starter",
                "mrr_delta_usd": delta,
                "reason_code": "downgrade" if day == 47 else "noop",
                "actor": "self-serve" if day == 47 else "system",
                "notes": (
                    "self-serve downgrade via /billing/plan; 30-day notice"
                    if day == 47
                    else f"daily reconcile, no plan change ({day})"
                ),
            }
        )
    body = "\n".join(json.dumps(r, sort_keys=True) for r in rows)
    return (
        "rowset: bigquery.run_query\n"
        f"rows_returned: {len(rows)}\n\n" + body + "\n"
    )


def _huge_payload() -> str:
    """~32 KB Slack-thread-style payload — exercises the high end of firewall."""
    msgs = []
    for i in range(1, 240):
        msgs.append(
            json.dumps(
                {
                    "ts": f"170000{i:04d}.000{i:03d}",
                    "user": "U-OPS" if i % 5 else "U-CSM",
                    "channel": "#incidents",
                    "text": (
                        "ack: tracking the MRR delta incident for C-12345 "
                        f"(turn {i}, 30s monitor still firing); "
                        "ETA 10 minutes on the dashboard refresh."
                    ),
                },
                sort_keys=True,
            )
        )
    return f"channel: #incidents\nmessages_returned: {len(msgs)}\n\n" + "\n".join(msgs) + "\n"


_SCENARIOS: tuple[GatewayScenario, ...] = (
    GatewayScenario(
        name="tiny_no_firewall",
        routing_query="list paid invoices for the past week",
        intent="billing.invoices.list",
        upstream_text=_tiny_payload(),
        user_query="Did all invoices clear?",
    ),
    GatewayScenario(
        name="medium_crm",
        routing_query="Search CRM accounts by name or domain to find Acme subsidiaries",
        intent="crm.accounts.search",
        upstream_text=_medium_payload(),
        user_query="Find all Acme subsidiaries on growth plans.",
    ),
    GatewayScenario(
        name="bigquery_rowset",
        routing_query="Execute a BigQuery query to find MRR delta rows for customer C-12345",
        intent="bigquery.run_query",
        upstream_text=_large_payload(),
        user_query="Why did customer C-12345's MRR drop last month?",
    ),
    GatewayScenario(
        name="slack_thread_review",
        routing_query="Search Slack message history in the incidents channel",
        intent="slack.search",
        upstream_text=_huge_payload(),
        user_query="Review what the team said about the C-12345 incident.",
    ),
)


def _build_catalog() -> Catalog:
    catalog = Catalog()
    for item in load_catalog_yaml(gateway_catalog_path()):
        catalog.register(item)
    return catalog


def _run_scenario(
    scenario: GatewayScenario, catalog: Catalog, router: Router
) -> GatewayScenarioResult:
    """Execute one scenario and return its metrics."""
    mgr = ContextManager(
        budget=ContextBudget(route=1500, call=2000, interpret=3000, answer=4000)
    )
    mgr.ingest_sync(ContextItem(id="u1", kind=ItemKind.user_turn, text=scenario.user_query))

    result = router.route(scenario.routing_query)
    shortlist = result.candidate_ids
    chosen = scenario.intent if scenario.intent in shortlist else shortlist[0]
    cards = make_choice_cards(
        result.candidate_items,
        scores=dict(zip(shortlist, result.scores, strict=False)),
    )

    mgr.ingest_sync(
        ContextItem(
            id="tc1",
            kind=ItemKind.tool_call,
            text=f"{chosen}(...)",
            parent_id="u1",
        )
    )
    raw_text = scenario.upstream_text
    raw_chars = len(raw_text)
    item, _envelope = mgr.ingest_mcp_result(
        tool_call_id="tc1",
        mcp_result={"content": [{"type": "text", "text": raw_text}], "isError": False},
        tool_name=chosen,
        firewall_threshold=FIREWALL_THRESHOLD,
    )
    summary_chars = len(item.text)
    firewall_triggered = raw_chars > FIREWALL_THRESHOLD
    answer = mgr.build_sync(phase=Phase.answer, query=scenario.routing_query)

    reduction = 100.0 * (1.0 - summary_chars / max(raw_chars, 1))
    return GatewayScenarioResult(
        name=scenario.name,
        catalog_tools=len(catalog.all()),
        exposed_choice_cards=len(cards),
        raw_result_chars=raw_chars,
        injected_summary_chars=summary_chars,
        firewall_reduction_pct=round(reduction, 2),
        firewall_triggered=firewall_triggered,
        final_prompt_tokens=answer.stats.prompt_tokens,
        selected_tool_id=chosen,
        chosen_was_intent=(chosen == scenario.intent),
    )


def _build_report() -> dict[str, Any]:
    """Run every scenario and assemble the JSON report (sorted, deterministic)."""
    catalog = _build_catalog()
    items = catalog.all()
    graph = TreeBuilder(max_children=10).build(items)
    router = Router(graph, items=items, top_k=5)

    rows: list[GatewayScenarioResult] = []
    for scenario in _SCENARIOS:
        rows.append(_run_scenario(scenario, catalog, router))

    rows.sort(key=lambda r: r.name)
    reductions = [row.firewall_reduction_pct for row in rows if row.firewall_triggered]
    return {
        "benchmark_version": BENCHMARK_VERSION,
        "catalog_source": "contextweaver.data:mcp_gateway_catalog.yaml",
        "firewall_threshold": FIREWALL_THRESHOLD,
        "scenarios": [asdict(row) for row in rows],
        "summary": {
            "scenario_count": len(rows),
            "firewall_triggered_count": sum(1 for r in rows if r.firewall_triggered),
            "firewall_no_op_count": sum(1 for r in rows if not r.firewall_triggered),
            "firewall_reduction_min_pct": round(min(reductions), 2) if reductions else 0.0,
            "firewall_reduction_max_pct": round(max(reductions), 2) if reductions else 0.0,
            "firewall_reduction_mean_pct": (
                round(sum(reductions) / len(reductions), 2) if reductions else 0.0
            ),
        },
    }


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python benchmarks/gateway_benchmark.py``."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--output",
        type=Path,
        default=_ROOT / "benchmarks" / "results" / "gateway_latest.json",
        help="Path to write the benchmark report (JSON).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail (exit 1) when the regenerated report differs from the on-disk file.",
    )
    args = parser.parse_args(argv)

    report = _build_report()
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"

    if args.check:
        if not args.output.exists():
            print(f"missing baseline: {args.output}", file=sys.stderr)
            return 1
        existing = args.output.read_text(encoding="utf-8")
        if existing != rendered:
            print(
                f"gateway benchmark drifted from {args.output} — re-run without --check to refresh.",
                file=sys.stderr,
            )
            return 1
        print(f"gateway benchmark byte-stable against {args.output}")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    summary = report["summary"]
    print(f"gateway benchmark wrote {args.output}")
    print(
        "  scenarios={count}  firewall_triggered={trig}  "
        "reduction range = {lo}% – {hi}%  (mean {mean}%)".format(
            count=summary["scenario_count"],
            trig=summary["firewall_triggered_count"],
            lo=summary["firewall_reduction_min_pct"],
            hi=summary["firewall_reduction_max_pct"],
            mean=summary["firewall_reduction_mean_pct"],
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
