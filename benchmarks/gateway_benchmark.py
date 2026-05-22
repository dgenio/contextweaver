"""Gateway-scenario benchmark suite (issue #270).

Turns the single-call "Single-call gateway scenario" anecdote in
``docs/benchmarks.md`` (98.8 % firewall reduction on one rowset) into a
**measured range** across 5 gateway-shaped scenarios. All scenarios:

- Reuse the 60-tool MCP Context Gateway catalog packaged at
  ``contextweaver.data:mcp_gateway_catalog.yaml`` (issue #264).
- Use different routing queries against the same catalog.
- Use different mocked upstream result sizes, including one tiny payload
  (< 500 chars — firewall correctly no-ops) and one large payload
  (~16 KB — firewall collapses to a compact summary).
- Are fully deterministic (fixed seeds, no network, no LLM, no real MCP
  server).

For every scenario the harness records:

- ``catalog_tools`` (60 across all scenarios)
- ``exposed_choice_cards`` (top-k cards exposed to the model)
- ``raw_result_chars`` (size of the upstream payload)
- ``injected_summary_chars`` (size after the firewall)
- ``firewall_reduction_pct`` (1 - injected/raw)
- ``final_prompt_tokens`` / ``final_prompt_chars`` (answer-phase build)
- ``artifact_created`` (whether the firewall stored the raw bytes)

Output: ``benchmarks/results/gateway_latest.json`` (committed alongside
the regular scorecard's ``latest.json``).

Usage::

    python benchmarks/gateway_benchmark.py
    python benchmarks/gateway_benchmark.py --output /tmp/gateway.json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from contextweaver.config import ContextBudget  # noqa: E402
from contextweaver.context.manager import ContextManager  # noqa: E402
from contextweaver.data import gateway_catalog_path  # noqa: E402
from contextweaver.protocols import CharDivFourEstimator  # noqa: E402
from contextweaver.routing.cards import make_choice_cards  # noqa: E402
from contextweaver.routing.catalog import Catalog, load_catalog_yaml  # noqa: E402
from contextweaver.routing.router import Router  # noqa: E402
from contextweaver.routing.tree import TreeBuilder  # noqa: E402
from contextweaver.types import ContextItem, ItemKind, Phase  # noqa: E402

# Issue #264 + #270: the 60-tool catalog now ships inside ``contextweaver.data``
# rather than under ``examples/`` so the benchmark works from a wheel install.
_CATALOG_PATH = gateway_catalog_path()
_BUDGET = ContextBudget(route=1500, call=2000, interpret=3000, answer=4000)
_ESTIMATOR = CharDivFourEstimator()
_BENCHMARK_VERSION = "1.0"


@dataclass
class GatewayScenarioStats:
    """Per-scenario stats emitted into ``gateway_latest.json``."""

    scenario: str
    user_query: str
    routing_query: str
    chosen_tool: str
    catalog_tools: int
    exposed_choice_cards: int
    raw_result_chars: int
    injected_summary_chars: int
    firewall_reduction_pct: float
    artifact_created: bool
    final_prompt_tokens: int
    final_prompt_chars: int


def _scenarios() -> list[dict[str, Any]]:
    """Return the deterministic scenario shapes.

    Five scenarios, ordered so the resulting range covers both ends of
    the firewall's operating envelope (tiny no-op result through ~16 KB
    full-collapse).
    """
    big_rowset = _big_rowset_text()
    medium_log = _medium_log_text()
    return [
        {
            "name": "tiny_ack",
            "user_query": "Acknowledge the pending PagerDuty incident PD-9931",
            "routing_query": "Acknowledge a PagerDuty incident",
            "intent": "pagerduty.incidents.ack",
            "upstream_text": "ok (incident PD-9931 acknowledged)",
        },
        {
            "name": "small_post",
            "user_query": "DM alice on Slack to confirm the rollback",
            "routing_query": "Send a direct Slack message to alice",
            "intent": "slack.dm.send",
            "upstream_text": (
                "ok — message posted to @alice (thread_ts=1716309042.000300, channel=alice-dm)"
            ),
        },
        {
            "name": "medium_ticket",
            "user_query": "Search Linear for the C-12345 plan-change ticket",
            "routing_query": "Search Linear tickets matching customer C-12345 plan change",
            "intent": "linear.tickets.search",
            "upstream_text": (
                "ticket TKT-742 — 'Self-serve downgrade for C-12345 (Growth → "
                "Starter)'\nstatus: closed\nowner: alice@example.com\n"
                "closed_at: 2026-03-18T17:42Z\n"
                "comments: 4 (last: alice@example.com 2026-03-18T17:38Z)\n"
                "link: https://linear.app/ops/issue/TKT-742"
            ),
        },
        {
            "name": "large_log",
            "user_query": "Pull recent error logs for the api-gateway outage window",
            "routing_query": "Query the events table for api-gateway error logs",
            "intent": "analytics.events.query",
            "upstream_text": medium_log,
        },
        {
            "name": "bigquery_rowset",
            "user_query": "Why did customer C-12345's MRR drop last month?",
            "routing_query": (
                "Execute a BigQuery query to find MRR delta rows for customer C-12345"
            ),
            "intent": "bigquery.run_query",
            "upstream_text": big_rowset,
        },
    ]


def _big_rowset_text() -> str:
    """16 KB rowset — mirrors the architecture's mocked BigQuery body."""
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
                    "self-serve downgrade via /billing/plan; "
                    "30-day notice satisfied; "
                    "retained 1 seat on Growth"
                    if day == 47
                    else f"daily reconcile, no plan change ({day})"
                ),
            }
        )
    body = "\n".join(json.dumps(r, sort_keys=True) for r in rows)
    return (
        "rowset: bigquery.run_query\n"
        "project: ops-analytics-prod\n"
        f"rows_returned: {len(rows)}\n"
        "schema: date STRING, customer_id STRING, plan STRING, "
        "mrr_delta_usd INT64, reason_code STRING, actor STRING, notes STRING\n\n" + body + "\n"
    )


def _medium_log_text() -> str:
    """~5 KB log dump — crosses the firewall threshold but smaller than rowset."""
    events = []
    for i in range(120):
        events.append(
            {
                "ts": f"2026-05-15T18:{i // 5:02d}:{(i % 5) * 12:02d}Z",
                "level": "ERROR" if i % 4 == 0 else "INFO",
                "msg": (
                    f"upstream timeout against payments-svc after {120 + (i % 60)}ms"
                    if i % 4 == 0
                    else f"request {i} handled in {15 + (i % 30)}ms"
                ),
                "trace_id": f"trace-{i:04d}",
            }
        )
    return json.dumps({"service": "api-gateway", "events": events})


def _select_tool(shortlist: list[str], intent: str) -> str:
    return intent if intent in shortlist else shortlist[0]


def _run_one(catalog: Catalog, router: Router, spec: dict[str, Any]) -> GatewayScenarioStats:
    """Walk a single scenario end-to-end and return its stats row."""
    mgr = ContextManager(budget=_BUDGET, estimator=_ESTIMATOR)
    u_id = f"u_{spec['name']}"
    mgr.ingest_sync(ContextItem(id=u_id, kind=ItemKind.user_turn, text=spec["user_query"]))

    route = router.route(spec["routing_query"])
    shortlist = route.candidate_ids
    chosen = _select_tool(shortlist, spec["intent"])
    cards = make_choice_cards(
        route.candidate_items,
        scores=dict(zip(shortlist, route.scores, strict=False)),
    )

    tc_id = f"tc_{spec['name']}"
    mgr.ingest_sync(
        ContextItem(
            id=tc_id,
            kind=ItemKind.tool_call,
            text=f"{chosen}({spec['routing_query'][:30]}...)",
            parent_id=u_id,
        )
    )
    raw_text = spec["upstream_text"]
    mcp_result = {"content": [{"type": "text", "text": raw_text}], "isError": False}
    item, _envelope = mgr.ingest_mcp_result(
        tool_call_id=tc_id,
        mcp_result=mcp_result,
        tool_name=chosen,
        firewall_threshold=2000,
    )
    injected = len(item.text)
    raw_chars = len(raw_text)
    reduction = 100.0 * (1.0 - injected / max(raw_chars, 1))

    pack = mgr.build_sync(phase=Phase.answer, query=spec["routing_query"])

    return GatewayScenarioStats(
        scenario=spec["name"],
        user_query=spec["user_query"],
        routing_query=spec["routing_query"],
        chosen_tool=chosen,
        catalog_tools=len(catalog.all()),
        exposed_choice_cards=len(cards),
        raw_result_chars=raw_chars,
        injected_summary_chars=injected,
        firewall_reduction_pct=round(reduction, 1),
        artifact_created=item.artifact_ref is not None,
        final_prompt_tokens=pack.stats.prompt_tokens,
        final_prompt_chars=len(pack.prompt),
    )


def run_all() -> dict[str, Any]:
    """Run every gateway scenario and return the aggregated JSON payload."""
    catalog = Catalog()
    for item in load_catalog_yaml(_CATALOG_PATH):
        catalog.register(item)
    items = catalog.all()
    graph = TreeBuilder(max_children=10).build(items)
    router = Router(graph, items=items, top_k=5)

    rows = [_run_one(catalog, router, spec) for spec in _scenarios()]
    reductions = [row.firewall_reduction_pct for row in rows]
    raw_chars = [row.raw_result_chars for row in rows]
    summary_chars = [row.injected_summary_chars for row in rows]

    payload: dict[str, Any] = {
        "benchmark_version": _BENCHMARK_VERSION,
        "catalog_path": "contextweaver.data:mcp_gateway_catalog.yaml",
        "scenarios": [asdict(row) for row in rows],
        "aggregate": {
            "n_scenarios": len(rows),
            "firewall_reduction_min_pct": min(reductions),
            "firewall_reduction_max_pct": max(reductions),
            "raw_chars_min": min(raw_chars),
            "raw_chars_max": max(raw_chars),
            "injected_summary_chars_min": min(summary_chars),
            "injected_summary_chars_max": max(summary_chars),
        },
    }
    return payload


def main() -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=_ROOT / "benchmarks" / "results" / "gateway_latest.json",
        help="Path to write the gateway benchmark JSON.",
    )
    args = parser.parse_args()
    payload = run_all()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    agg = payload["aggregate"]
    print(
        f"Gateway benchmark ({agg['n_scenarios']} scenarios): "
        f"firewall reduction {agg['firewall_reduction_min_pct']:.1f}%–"
        f"{agg['firewall_reduction_max_pct']:.1f}%, "
        f"raw payload {agg['raw_chars_min']:,}–{agg['raw_chars_max']:,} chars; "
        f"wrote {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
