"""contextweaver inside a LangGraph agent loop (#326).

A support/ops agent fronting ~36 tools, driven by a **LangGraph**
``StateGraph``. The boundary is the point of this example:

- **LangGraph owns control flow** — the route -> execute -> answer graph,
  and the per-turn invocation loop.
- **contextweaver owns context compilation** — it narrows the catalog to a
  ChoiceCard shortlist (route), firewalls large tool results (interpret),
  and assembles a budget-aware answer prompt with dependency-chain
  preservation (answer).
- **Tool execution stays outside contextweaver** — the nodes call mocked
  backends; contextweaver never executes a tool.

The scenario is two turns, the second a follow-up that depends on the first
turn's (firewalled) tool result, so the answer build has to pull the prior
result back in via dependency closure.

This runs with **no API keys and no network**: the "model" decision at each
node is a deterministic intent map standing in for an LLM holding the
ChoiceCard shortlist in its prompt. The comments mark exactly where a real
LLM call would go.

LangGraph is optional. When it is installed the real ``StateGraph`` drives
the loop; otherwise an equivalent hand-rolled loop calls the same node
functions in the same order, so the output is identical either way and the
example still runs under a bare ``pip install contextweaver``. Install the
framework with ``pip install 'contextweaver[langgraph]'``.

Run standalone::

    python examples/architectures/langgraph_agent_loop/main.py

Or via ``make example`` (the ``architectures`` umbrella target).
"""

from __future__ import annotations

import functools
from typing import Any, TypedDict, cast

from contextweaver.config import ContextBudget
from contextweaver.context.manager import ContextManager
from contextweaver.routing.cards import make_choice_cards, render_cards_text
from contextweaver.routing.catalog import Catalog, generate_sample_catalog
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import ContextItem, ItemKind, Phase, SelectableItem

try:  # Optional framework — see module docstring.
    from langgraph.graph import END, START, StateGraph

    _HAS_LANGGRAPH = True
except ImportError:  # pragma: no cover - exercised only without the extra
    _HAS_LANGGRAPH = False


class TurnState(TypedDict, total=False):
    """Per-turn LangGraph state. Each key is a channel that persists across
    nodes; nodes return only the fields they update. The heavy context
    (ContextManager, routing graph) lives in :class:`_Session`, not here."""

    turn_id: str
    query: str
    intent: str
    shortlist: list[str]
    chosen: str
    cards_text: str
    raw_chars: int
    firewall_note: str
    answer_prompt: str
    answer_chars: int
    closures: int
    included: int


# Hero tools with strong, query-aligned vocabulary so routing is
# deterministic; the rest of the catalog is generated for realistic
# routing pressure (see ``_build_catalog``).
_HERO_TOOLS: list[SelectableItem] = [
    SelectableItem(
        id="infra.logs_search",
        kind="tool",
        name="logs_search",
        description="Search recent service error logs and stack traces by service and time window",
        tags=["logs", "errors", "incident", "infra", "observability"],
        namespace="infra",
        cost_hint=0.3,
    ),
    SelectableItem(
        id="infra.metrics_query",
        kind="tool",
        name="metrics_query",
        description="Query service metrics (latency, error rate, saturation) over a time range",
        tags=["metrics", "latency", "infra", "observability"],
        namespace="infra",
        cost_hint=0.2,
    ),
    SelectableItem(
        id="incident.draft_note",
        kind="tool",
        name="draft_note",
        description="Draft an incident summary note describing root cause, impact, and next steps",
        tags=["incident", "summary", "note", "write"],
        namespace="incident",
        side_effects=True,
        cost_hint=0.1,
    ),
    SelectableItem(
        id="incident.page_oncall",
        kind="tool",
        name="page_oncall",
        description="Page the on-call engineer for a service with an incident severity",
        tags=["incident", "page", "oncall", "write"],
        namespace="incident",
        side_effects=True,
        cost_hint=0.1,
    ),
]

# A two-turn ops session. Each entry is ``(turn_id, user_text, intent)``
# where ``intent`` is the tool an LLM would pick *given the routed
# shortlist*. The second turn depends on the first turn's tool result.
TRANSCRIPT: list[tuple[str, str, str]] = [
    (
        "t1",
        "Our checkout API is throwing 500s — pull the recent error logs for the payments service",
        "infra.logs_search",
    ),
    (
        "t2",
        "Summarize the likely root cause from those logs and draft an incident note",
        "incident.draft_note",
    ),
]


@functools.cache
def _large_log_dump() -> str:
    """Return a ~8 KB synthetic error-log payload (> firewall threshold)."""
    lines = [
        "service: payments  window: last 15m  level>=ERROR",
        "",
    ]
    for i in range(110):
        ts = f"2026-05-28T09:{i % 60:02d}:{(i * 7) % 60:02d}Z"
        lines.append(
            f"{ts} ERROR [payments] HTTP 500 POST /charge "
            f"trace_id=tr-{4000 + i} err=ConnectionResetError "
            f"upstream=ledger-db pool_wait_ms={(i * 13) % 900} "
            f"msg='connection reset by peer while acquiring ledger lock'"
        )
    return "\n".join(lines) + "\n"


_TOOL_RESPONSES: dict[str, str] = {
    "infra.logs_search": _large_log_dump(),
    "incident.draft_note": (
        "incident note drafted: 'Payments 500s caused by ledger-db connection "
        "resets under lock contention; mitigation: raise pool size + add retry.'"
    ),
}


def _build_catalog() -> Catalog:
    """Generate a ~36-tool catalog: synthetic bulk + query-aligned hero tools."""
    catalog = Catalog()
    for raw in generate_sample_catalog(n=32, seed=11):
        catalog.register(SelectableItem.from_dict(raw))
    for hero in _HERO_TOOLS:
        catalog.register(hero)
    return catalog


def _select_from_shortlist(shortlist: list[str], intent: str) -> str:
    """Pick *intent* if routed into the shortlist, else fall back to the top card.

    This is the stand-in for the LLM decision: a real agent would put the
    rendered ChoiceCards into its prompt and let the model choose. The
    intent map keeps the example deterministic and key-free.
    """
    return intent if intent in shortlist else shortlist[0]


class _Session:
    """Holds the cross-turn agent state shared by the graph nodes.

    The :class:`ContextManager` (and the routing graph) live here, *outside*
    the LangGraph state, because they are not serialisable and because this
    is exactly the boundary the example illustrates: the framework threads
    lightweight per-turn state; contextweaver owns the heavy context.
    """

    def __init__(self) -> None:
        self.catalog = _build_catalog()
        items = self.catalog.all()
        self.items = items
        graph = TreeBuilder(max_children=10).build(items)
        self.router = Router(graph, items=items, beam_width=3, top_k=5)
        budget = ContextBudget(route=1200, call=2000, interpret=2000, answer=2600)
        self.mgr = ContextManager(budget=budget)
        self.step = 0

    def render_all_tool_descriptions(self) -> str:
        """The naive baseline: every tool description dumped into the prompt."""
        return "\n".join(f"- {it.id} ({it.namespace}): {it.description}" for it in self.items)


def _make_nodes(session: _Session) -> dict[str, Any]:
    """Build the three graph node callables bound to *session*.

    Each node takes the per-turn state dict and returns the fields it
    updated — the signature LangGraph expects, and equally usable by the
    hand-rolled fallback loop.
    """

    def route_node(state: dict[str, Any]) -> dict[str, Any]:
        """contextweaver route phase: narrow the catalog to a shortlist."""
        query = state["query"]
        session.mgr.ingest_sync(
            ContextItem(id=state["turn_id"] + "-u", kind=ItemKind.user_turn, text=query)
        )
        result = session.router.route(query)
        shortlist = result.candidate_ids
        # --- where a real LLM call would go -------------------------------
        # The agent would receive these ChoiceCards in its prompt and pick a
        # tool. We substitute a deterministic intent map instead.
        cards = make_choice_cards(
            result.candidate_items,
            scores=dict(zip(result.candidate_ids, result.scores, strict=False)),
        )
        chosen = _select_from_shortlist(shortlist, state["intent"])
        return {"shortlist": shortlist, "chosen": chosen, "cards_text": render_cards_text(cards)}

    def execute_node(state: dict[str, Any]) -> dict[str, Any]:
        """Tool execution (outside contextweaver) + interpret-phase firewall."""
        chosen = state["chosen"]
        tc_id = state["turn_id"] + "-tc"
        session.mgr.ingest_sync(
            ContextItem(
                id=tc_id,
                kind=ItemKind.tool_call,
                text=f"{chosen}(...)",
                parent_id=state["turn_id"] + "-u",
            )
        )
        # The framework (or a real tool runtime) executes the tool. Here it
        # is mocked; contextweaver only sees the *result*.
        raw_output = _TOOL_RESPONSES.get(chosen, f"{chosen} returned ok")
        item, _envelope = session.mgr.ingest_tool_result_sync(
            tool_call_id=tc_id,
            raw_output=raw_output,
            tool_name=chosen,
            firewall_threshold=2000,
        )
        fired = item.artifact_ref is not None and len(raw_output) > 2000
        note = ""
        if fired and item.artifact_ref is not None:
            note = (
                f"{len(raw_output):,} chars -> {len(item.text):,}-char summary "
                f"(artifact {item.artifact_ref.handle})"
            )
        return {"raw_chars": len(raw_output), "firewall_note": note}

    def answer_node(state: dict[str, Any]) -> dict[str, Any]:
        """contextweaver answer phase: budget-aware prompt with closures."""
        pack = session.mgr.build_sync(phase=Phase.answer, query=state["query"])
        return {
            "answer_prompt": pack.prompt,
            "answer_chars": len(pack.prompt),
            "closures": pack.stats.dependency_closures,
            "included": pack.stats.included_count,
        }

    return {"route": route_node, "execute": execute_node, "answer": answer_node}


def _run_turn_langgraph(
    session: _Session, nodes: dict[str, Any], turn: dict[str, Any]
) -> dict[str, Any]:
    """Drive one turn through a real LangGraph ``StateGraph``."""
    builder = StateGraph(TurnState)
    builder.add_node("route", nodes["route"])
    builder.add_node("execute", nodes["execute"])
    builder.add_node("answer", nodes["answer"])
    builder.add_edge(START, "route")
    builder.add_edge("route", "execute")
    builder.add_edge("execute", "answer")
    builder.add_edge("answer", END)
    graph = builder.compile()
    # LangGraph's typed ``Pregel.invoke`` signature varies across versions (and
    # is absent when the optional langgraph extra is not installed). The loop
    # deliberately passes a plain state dict, so invoke through ``Any`` to keep
    # `make type` deterministic regardless of the installed langgraph version.
    return dict(cast(Any, graph).invoke(turn))


def _run_turn_fallback(
    session: _Session, nodes: dict[str, Any], turn: dict[str, Any]
) -> dict[str, Any]:
    """Drive one turn through an equivalent hand-rolled loop (no LangGraph)."""
    state = dict(turn)
    for name in ("route", "execute", "answer"):
        state.update(nodes[name](state))
    return state


def _print_header(title: str) -> None:
    """Print a section banner consistent with the other architecture scripts."""
    print()
    print("=" * 76)
    print(title)
    print("=" * 76)


def main() -> None:
    """Run the LangGraph agent loop end-to-end."""
    _print_header("contextweaver -- LangGraph agent-loop reference architecture")
    engine = "langgraph" if _HAS_LANGGRAPH else "fallback (langgraph not installed)"
    session = _Session()
    nodes = _make_nodes(session)
    print(f"agent loop engine: {engine}")
    n_namespaces = len({i.namespace for i in session.items})
    print(f"catalog: {len(session.items)} tools across {n_namespaces} namespaces")

    run_turn = _run_turn_langgraph if _HAS_LANGGRAPH else _run_turn_fallback

    for turn_id, user_text, intent in TRANSCRIPT:
        _print_header(f"Turn {turn_id}")
        print(f"user: {user_text}")

        # Naive baseline for this turn: dump every tool description (route)
        # plus, on the answer side, the raw tool result the firewall would
        # otherwise externalise.
        naive_tools = session.render_all_tool_descriptions()

        state = run_turn(session, nodes, {"turn_id": turn_id, "query": user_text, "intent": intent})

        print(f"routed shortlist: {state['shortlist']}")
        print(
            f"chosen: {state['chosen']}  "
            f"(intent={intent!r}, {'in shortlist' if intent in state['shortlist'] else 'fallback'})"
        )
        print(
            f"route prompt:  naive all-tools {len(naive_tools):,} chars  ->  "
            f"ChoiceCards {len(state['cards_text']):,} chars"
        )
        if state.get("firewall_note"):
            print(f"firewall: {state['firewall_note']}")
        print(
            f"answer prompt: {state['answer_chars']:,} chars  "
            f"(included={state['included']}, dependency_closures={state['closures']})"
        )

    _print_header("What this showed")
    print("- LangGraph owned the route -> execute -> answer control flow.")
    print("- contextweaver bounded the catalog to a 5-card shortlist each turn.")
    print("- the large log result was firewalled to a summary on turn t1.")
    print("- turn t2's answer carried turn t1's firewalled result forward")
    print("  (cross-turn retention); the dependency_closure stage keeps every")
    print("  tool result paired with its originating tool call.")


if __name__ == "__main__":
    main()
