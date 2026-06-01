"""contextweaver routes -> ChainWeaver executes -> contextweaver ingests (#353).

The strongest "sum > parts" pairing in the Weaver stack is the handoff from
contextweaver (the *router* / context compiler) to ChainWeaver (the
deterministic *flow* executor):

1. **Route (contextweaver).** A :class:`Router` shortlists a small catalog
   of ordinary tools *plus* ChainWeaver flows imported via
   :func:`~contextweaver.adapters.chainweaver.load_chainweaver_export`
   (issue #334). The flows carry ``kind="flow"`` and route like any other
   candidate.
2. **Hand off at the contract level (issue #320).** The
   :class:`~contextweaver.envelope.RoutingDecision` is mapped to the neutral
   weaver-spec ``RoutingDecision`` via
   :func:`~contextweaver.adapters.weaver_contracts.to_weaver_routing_decision`
   when the ``[weaver-spec]`` extra is installed. The decision is *advisory*:
   contextweaver selects a candidate, it does not execute or authorise it.
3. **Execute (ChainWeaver).** A tiny in-process stub stands in for the
   ChainWeaver runtime — there is **no hard dependency** on ChainWeaver. It
   runs the selected flow deterministically and returns a large raw result.
4. **Ingest the result (contextweaver firewall).** The raw flow output goes
   back through :meth:`ContextManager.ingest_tool_result_sync`; the firewall
   stores the bytes out-of-band and the prompt only sees a compact summary,
   which is then mapped to a weaver-spec ``Frame``.

Everything here is deterministic and offline: the "ChainWeaver" executor is
canned. The point is to demonstrate the route -> execute -> ingest seam, not
to integrate with a live runtime. See ``docs/weaver_spec_mapping.md`` for the
contract boundary this example exercises.

Run standalone::

    python examples/architectures/contextweaver_to_chainweaver/main.py

Or via ``make example`` (the ``architectures`` umbrella target).
"""

from __future__ import annotations

import json
from typing import Any

from contextweaver.adapters.chainweaver import load_chainweaver_export
from contextweaver.config import ContextBudget
from contextweaver.context.manager import ContextManager
from contextweaver.envelope import RoutingDecision
from contextweaver.routing.catalog import Catalog
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import ContextItem, ItemKind, Phase, SelectableItem

# Ordinary single-step tools the agent already has.
_TOOLS: list[SelectableItem] = [
    SelectableItem(
        id="crm:lookup_customer",
        kind="tool",
        name="lookup_customer",
        description="Look up a single customer record by id.",
        namespace="crm",
        tags=["customer"],
    ),
    SelectableItem(
        id="crm:send_email",
        kind="tool",
        name="send_email",
        description="Send a one-off email to a customer.",
        namespace="crm",
        tags=["comm"],
    ),
    SelectableItem(
        id="billing:get_invoice",
        kind="tool",
        name="get_invoice",
        description="Fetch a single invoice PDF by invoice number.",
        namespace="billing",
        tags=["billing"],
    ),
]

# A ChainWeaver *flow export* — plain data, no ChainWeaver install required.
# Each flow is a multi-step capability ChainWeaver executes deterministically.
_CHAINWEAVER_EXPORT: dict[str, Any] = {
    "flows": [
        {
            "id": "customer_summary_flow",
            "name": "Summarize customer history",
            "description": (
                "Multi-step flow: pull a customer's recent orders, invoices, and "
                "support tickets, then produce a consolidated history summary."
            ),
            "version": "1.2.0",
            "input_schema": {
                "type": "object",
                "properties": {"customer_id": {"type": "string"}},
                "required": ["customer_id"],
            },
            "output_schema": {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
            },
            "tags": ["customer", "summary", "billing"],
        },
        {
            "id": "dunning_flow",
            "name": "Run dunning sequence",
            "description": "Multi-step flow that escalates overdue-invoice reminders.",
            "version": "0.4.1",
            "input_schema": {
                "type": "object",
                "properties": {"invoice_id": {"type": "string"}},
            },
            "tags": ["billing", "collections"],
        },
    ]
}

# The request that should route to the customer-summary *flow* rather than any
# single-step tool, because no one tool answers it on its own.
QUERY = "summarize this customer's recent billing and order history"


def _print_header(title: str) -> None:
    """Print a section banner consistent with the other architecture scripts."""
    print()
    print("=" * 76)
    print(title)
    print("=" * 76)


def _build_catalog() -> Catalog:
    """Combine ordinary tools with imported ChainWeaver flows in one catalog."""
    catalog = Catalog()
    for tool in _TOOLS:
        catalog.register(tool)
    for flow_item in load_chainweaver_export(_CHAINWEAVER_EXPORT).all():
        catalog.register(flow_item)
    return catalog


def _host_candidate(
    decision: RoutingDecision, item_lookup: dict[str, SelectableItem]
) -> dict[str, Any]:
    """Project the advisory routing decision into a host-side candidate dict.

    This mirrors the neutral ``ExecutionCandidate`` shape described in issue
    #320 *without* binding to a weaver-spec type (the spec does not yet define
    one). A host resolves this candidate to a concrete runtime target — here,
    a ChainWeaver flow id carried in the item's metadata.
    """
    top_card = decision.choice_cards[0]
    item = item_lookup[top_card.id]
    return {
        "candidate_id": item.id,
        "candidate_type": item.kind,  # "flow" for a ChainWeaver flow
        "name": item.name,
        "confidence": round(top_card.score, 4) if top_card.score is not None else None,
        "reason_codes": ["choicecard_match", "phase_route"],
        "runtime": item.metadata.get("runtime"),
        "runtime_flow_id": item.metadata.get("chainweaver_flow_id"),
        "advisory": True,  # routing never grants execution rights
    }


class _StubChainWeaverRuntime:
    """Stand-in for the ChainWeaver runtime — deterministic, offline.

    A real integration would hand the selected flow id + inputs to ChainWeaver
    over its own transport. Here we return a canned, intentionally large result
    so the contextweaver firewall is load-bearing on the way back in.
    """

    def execute(self, flow_id: str, inputs: dict[str, Any]) -> str:
        """Return a canned multi-step flow result keyed by *flow_id*."""
        if flow_id == "customer_summary_flow":
            customer_id = inputs.get("customer_id", "unknown")
            orders = [
                {"order_id": f"ord-{i:04d}", "total": 19.0 + i, "status": "shipped"}
                for i in range(60)
            ]
            return json.dumps(
                {
                    "customer_id": customer_id,
                    "orders": orders,
                    "open_invoices": [{"invoice_id": "inv-9001", "amount_due": 240.0}],
                    "support_tickets": [{"ticket": "t-77", "state": "resolved"}],
                    "summary": (
                        f"Customer {customer_id}: 60 orders (all shipped), 1 open invoice "
                        "($240.00 due), 1 resolved support ticket."
                    ),
                }
            )
        return json.dumps({"flow_id": flow_id, "status": "ok"})


def main() -> None:
    """Run the route -> execute -> ingest scenario end-to-end."""
    _print_header("contextweaver -> ChainWeaver reference architecture")

    catalog = _build_catalog()
    item_lookup = {item.id: item for item in catalog.all()}
    flow_count = sum(1 for item in catalog.all() if item.kind == "flow")
    print(
        f"Loaded catalog: {len(item_lookup)} items "
        f"({flow_count} ChainWeaver flows + {len(item_lookup) - flow_count} tools)"
    )

    graph = TreeBuilder(max_children=20).build(catalog.all())
    router = Router(graph, items=catalog.all(), top_k=3)
    budget = ContextBudget(route=1500, call=2000, interpret=2500, answer=2500)
    mgr = ContextManager(budget=budget)

    # ------------------------------------------------------------------
    # 1. Route (contextweaver) — bounded shortlist; the flow should win.
    # ------------------------------------------------------------------
    _print_header("1. Route (contextweaver)")
    print(f"query: {QUERY}")
    mgr.ingest_sync(ContextItem(id="u1", kind=ItemKind.user_turn, text=QUERY))
    result = router.route(QUERY)
    print(f"shortlist: {result.candidate_ids}")
    selected_id = result.candidate_ids[0]
    selected = item_lookup[selected_id]
    print(f"selected:  {selected_id}  (kind={selected.kind!r})")
    routed_to_flow = selected.kind == "flow"
    print(f"routed to a ChainWeaver flow: {routed_to_flow}")

    # ------------------------------------------------------------------
    # 2. Hand off at the contract level (issue #320) — advisory only.
    # ------------------------------------------------------------------
    _print_header("2. Hand off (advisory routing decision)")
    decision = result.to_routing_decision(
        selected_item_id=selected_id,
        context_summary="route customer-history request to a ChainWeaver flow",
    )
    candidate = _host_candidate(decision, item_lookup)
    print("host-side ExecutionCandidate (neutral; resolved to a ChainWeaver flow):")
    print(json.dumps(candidate, indent=2))

    spec_mapped = False
    try:
        from contextweaver.adapters.weaver_contracts import to_weaver_routing_decision

        spec_decision = to_weaver_routing_decision(decision)
        spec_mapped = True
        print(f"weaver-spec RoutingDecision id: {spec_decision.id}")
    except Exception as exc:  # weaver_contracts not installed (core install)
        print(
            f"weaver-spec mapping skipped ({type(exc).__name__}); "
            "install contextweaver[weaver-spec]"
        )

    # ------------------------------------------------------------------
    # 3. Execute (ChainWeaver stub) — no hard dependency on ChainWeaver.
    # ------------------------------------------------------------------
    _print_header("3. Execute (ChainWeaver stub)")
    runtime = _StubChainWeaverRuntime()
    flow_id = selected.metadata["chainweaver_flow_id"]
    inputs = {"customer_id": "cust-42"}
    print(f"ChainWeaver.execute(flow_id={flow_id!r}, inputs={inputs})")
    raw_result = runtime.execute(flow_id, inputs)
    print(f"raw flow result: {len(raw_result):,} chars")

    # ------------------------------------------------------------------
    # 4. Ingest the result (contextweaver firewall) — large result compacted.
    # ------------------------------------------------------------------
    _print_header("4. Ingest result (contextweaver firewall)")
    mgr.ingest_sync(
        ContextItem(id="tc1", kind=ItemKind.tool_call, text=f"{flow_id}(...)", parent_id="u1")
    )
    item, envelope = mgr.ingest_tool_result_sync(
        tool_call_id="tc1",
        raw_output=raw_result,
        tool_name=flow_id,
        media_type="application/json",
        firewall_threshold=2000,
    )
    firewalled = item.artifact_ref is not None and len(raw_result) > 2000
    if firewalled:
        print(
            f"firewall: {len(raw_result):,} chars -> {len(item.text):,}-char summary "
            f"(artifact {item.artifact_ref.handle})"
        )

    frame_mapped = False
    if spec_mapped:
        from contextweaver.adapters.weaver_contracts import to_weaver_frame

        frame = to_weaver_frame(envelope, frame_id="frame-cust-42", capability_id=selected_id)
        frame_mapped = True
        print(f"weaver-spec Frame id: {frame.frame_id} (capability {frame.capability_id})")

    answer = mgr.build_sync(phase=Phase.answer, query=QUERY)
    ans_tokens = sum(answer.stats.tokens_per_section.values())
    print(
        f"answer prompt: included={answer.stats.included_count} "
        f"dropped={answer.stats.dropped_count} tokens={ans_tokens}"
    )

    # ------------------------------------------------------------------
    # Scoreboard.
    # ------------------------------------------------------------------
    _print_header("Scoreboard")
    print(f"routed to flow:        {routed_to_flow}")
    print(f"firewall fired:        {firewalled}")
    print(f"weaver-spec mapped:    decision={spec_mapped} frame={frame_mapped}")
    print(f"artifacts kept:        {len(list(mgr.artifact_store.list_refs()))}")
    print(
        "Seam: contextweaver ROUTES (advisory) -> ChainWeaver EXECUTES -> "
        "contextweaver INGESTS the result behind the firewall."
    )


if __name__ == "__main__":
    main()
