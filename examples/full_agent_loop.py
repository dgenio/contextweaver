"""Reference agent loop example demonstrating all four phases.

This script shows a deterministic, in-memory loop:
1. Route: shortlist candidate tools and show choice cards.
2. Call: inject only the selected tool schema into a call prompt.
3. Interpret: ingest a large tool result and let the firewall summarize it.
4. Answer: build the final response context from the accumulated history.
"""

from __future__ import annotations

import json

from contextweaver.config import ContextBudget
from contextweaver.context.manager import ContextManager
from contextweaver.routing.catalog import Catalog, generate_sample_catalog, load_catalog_dicts
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import ContextItem, ItemKind, Phase


def _token_count(pack_prompt_tokens: dict[str, int], header_footer_tokens: int) -> int:
    """Compute total tokens represented in BuildStats."""
    return sum(pack_prompt_tokens.values()) + header_footer_tokens


def _print_phase(
    name: str,
    prompt: str,
    stats_total: int,
    stats_included: int,
    stats_dropped: int,
    stats_dedup: int,
    stats_hf_tokens: int,
    stats_tokens_per_section: dict[str, int],
) -> None:
    """Print prompt text and compact diagnostics for one phase."""
    print(f"\n{'=' * 80}")
    print(f"PHASE: {name}")
    print(f"{'=' * 80}")
    print(prompt)
    print("\n--- BuildStats ---")
    print(f"total_candidates: {stats_total}")
    print(f"included_count: {stats_included}")
    print(f"dropped_count: {stats_dropped}")
    print(f"dedup_removed: {stats_dedup}")
    print(f"header_footer_tokens: {stats_hf_tokens}")
    print(f"tokens_per_section: {stats_tokens_per_section}")
    print(f"token_count: {_token_count(stats_tokens_per_section, stats_hf_tokens)}")


def _build_catalog() -> Catalog:
    """Create a deterministic catalog with >50 tools and explicit schemas on target tools."""
    raw = generate_sample_catalog(n=60, seed=42)

    # Add full schemas for two tools so call-phase hydration is meaningful.
    for item in raw:
        if item["id"] == "analytics.metrics.query":
            item["args_schema"] = {
                "type": "object",
                "properties": {
                    "metric": {"type": "string", "description": "Metric identifier"},
                    "window_days": {"type": "integer", "description": "Trailing time window"},
                    "group_by": {"type": "string", "description": "Aggregation bucket"},
                },
                "required": ["metric", "window_days"],
            }
            item["examples"] = [
                "metrics_query(metric='daily_active_users', window_days=30, group_by='day')"
            ]
            item["constraints"] = {"max_window_days": 365, "read_only": True}
        if item["id"] == "billing.reports.revenue":
            item["args_schema"] = {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "ISO date"},
                    "end_date": {"type": "string", "description": "ISO date"},
                },
                "required": ["start_date", "end_date"],
            }
            item["examples"] = ["revenue_report(start_date='2026-01-01', end_date='2026-01-31')"]
            item["constraints"] = {"read_only": True}
        if item["id"] == "billing.subscriptions.list":
            item["args_schema"] = {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "Filter status such as active, trialing, or canceled",
                    },
                    "limit": {"type": "integer", "description": "Maximum records to return"},
                },
                "required": [],
            }
            item["examples"] = ["subscriptions_list(status='active', limit=50)"]
            item["constraints"] = {"read_only": True}

    catalog = Catalog()
    for item in load_catalog_dicts(raw):
        catalog.register(item)
    return catalog


def _pick_tool(route_ids: list[str], catalog: Catalog) -> str:
    """Simulate model selection: prefer analytics.metrics.query, then any tool with a schema.

    Preferring the intended analytics tool ensures that the tool-call text and
    simulated result ingested later remain internally consistent with the selected
    tool's schema (the user query concerns daily-active-user trends).

    Raises:
        ValueError: If the router returned no candidate tools.
    """
    if not route_ids:
        raise ValueError(
            "Router returned no candidates. Ensure the catalog contains reachable items."
        )
    # Prefer the intended analytics tool — the query is about DAU trends.
    if "analytics.metrics.query" in route_ids:
        return "analytics.metrics.query"
    # Fall back to the first routed tool that carries an explicit schema.
    for item_id in route_ids:
        if catalog.get(item_id).args_schema:
            return item_id
    return route_ids[0]


def _simulate_large_result(tool_id: str) -> str:
    """Build a large deterministic JSON payload to trigger firewall summarization."""
    rows = []
    for idx in range(1, 101):
        rows.append(
            {
                "day": f"2026-03-{idx % 30 + 1:02d}",
                "metric": "daily_active_users",
                "value": 1500 + idx,
                "tool": tool_id,
            }
        )
    payload = {
        "status": "ok",
        "rows": rows,
        "summary": {
            "window_days": 30,
            "max": max(row["value"] for row in rows),
            "min": min(row["value"] for row in rows),
        },
    }
    return json.dumps(payload, sort_keys=True)


def main() -> None:
    """Run the full route -> call -> interpret -> answer loop."""
    budget = ContextBudget(route=500, call=800, interpret=600, answer=1000)
    manager = ContextManager(budget=budget)

    catalog = _build_catalog()
    items = catalog.all()
    graph = TreeBuilder(max_children=12).build(items)
    router = Router(graph, items=items, beam_width=3, top_k=6)

    user_query = "What is the trend of daily active users over the last 30 days?"
    manager.ingest(
        ContextItem(
            id="u1",
            kind=ItemKind.user_turn,
            text=user_query,
        )
    )

    # Phase 1: route prompt + choice cards from a large catalog.
    route_pack, cards, route_result = manager.build_route_prompt_sync(
        goal="Select the single best analytics tool for the user request.",
        query=user_query,
        router=router,
    )
    _print_phase(
        name="route",
        prompt=route_pack.prompt,
        stats_total=route_pack.stats.total_candidates,
        stats_included=route_pack.stats.included_count,
        stats_dropped=route_pack.stats.dropped_count,
        stats_dedup=route_pack.stats.dedup_removed,
        stats_hf_tokens=route_pack.stats.header_footer_tokens,
        stats_tokens_per_section=route_pack.stats.tokens_per_section,
    )
    print(f"choice_cards: {len(cards)}")
    print(f"routed_candidates: {route_result.candidate_ids}")

    selected_tool_id = _pick_tool(route_result.candidate_ids, catalog)
    selected = catalog.get(selected_tool_id)
    print(f"model_selected_tool_id: {selected_tool_id}")

    manager.ingest(
        ContextItem(
            id="a1",
            kind=ItemKind.agent_msg,
            text=f"I will call {selected_tool_id} to answer the question.",
            parent_id="u1",
        )
    )
    # _pick_tool() guarantees analytics.metrics.query for this DAU query, so the
    # hardcoded args below are internally consistent with the selected tool's schema.
    manager.ingest(
        ContextItem(
            id="tc1",
            kind=ItemKind.tool_call,
            text=(
                f"{selected_tool_id}(metric='daily_active_users', window_days=30, group_by='day')"
            ),
            parent_id="u1",
        )
    )

    # Phase 2: call prompt with only the selected tool schema injected.
    call_pack = manager.build_call_prompt_sync(
        tool_id=selected_tool_id,
        query=user_query,
        catalog=catalog,
    )
    _print_phase(
        name="call",
        prompt=call_pack.prompt,
        stats_total=call_pack.stats.total_candidates,
        stats_included=call_pack.stats.included_count,
        stats_dropped=call_pack.stats.dropped_count,
        stats_dedup=call_pack.stats.dedup_removed,
        stats_hf_tokens=call_pack.stats.header_footer_tokens,
        stats_tokens_per_section=call_pack.stats.tokens_per_section,
    )
    print(f"selected_schema_keys: {sorted(selected.args_schema.get('properties', {}).keys())}")

    # Execute (simulated): create a large tool result that exceeds firewall threshold.
    large_result = _simulate_large_result(selected_tool_id)
    processed_item, envelope = manager.ingest_tool_result_sync(
        tool_call_id="tc1",
        raw_output=large_result,
        tool_name=selected_tool_id,
        media_type="application/json",
        firewall_threshold=1200,
    )

    # Phase 3: interpret prompt includes firewall summary instead of raw payload.
    interpret_pack = manager.build_sync(phase=Phase.interpret, query=user_query)
    _print_phase(
        name="interpret",
        prompt=interpret_pack.prompt,
        stats_total=interpret_pack.stats.total_candidates,
        stats_included=interpret_pack.stats.included_count,
        stats_dropped=interpret_pack.stats.dropped_count,
        stats_dedup=interpret_pack.stats.dedup_removed,
        stats_hf_tokens=interpret_pack.stats.header_footer_tokens,
        stats_tokens_per_section=interpret_pack.stats.tokens_per_section,
    )
    print(f"raw_result_chars: {len(large_result)}")
    print(f"summary_chars: {len(processed_item.text)}")
    artifact_handle = processed_item.artifact_ref.handle if processed_item.artifact_ref else "none"
    print(f"artifact_ref: {artifact_handle}")
    print(f"facts_extracted: {len(envelope.facts)}")

    manager.ingest(
        ContextItem(
            id="a2",
            kind=ItemKind.agent_msg,
            text="I interpreted the metrics and will now draft the final response.",
            parent_id="u1",
        )
    )

    # Phase 4: answer prompt composes final context from prior phases.
    answer_pack = manager.build_sync(
        phase=Phase.answer,
        query="Summarize the 30-day active-user trend for the user.",
    )
    _print_phase(
        name="answer",
        prompt=answer_pack.prompt,
        stats_total=answer_pack.stats.total_candidates,
        stats_included=answer_pack.stats.included_count,
        stats_dropped=answer_pack.stats.dropped_count,
        stats_dedup=answer_pack.stats.dedup_removed,
        stats_hf_tokens=answer_pack.stats.header_footer_tokens,
        stats_tokens_per_section=answer_pack.stats.tokens_per_section,
    )


if __name__ == "__main__":
    main()
