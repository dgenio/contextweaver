"""Bring-your-own-tools cookbook recipe.

Wraps plain Python callables as ``SelectableItem`` objects, registers them
in a ``Catalog``, routes a user query through the bounded-choice DAG, and
shows how to feed the routed shortlist into your own agent loop.

This recipe deliberately avoids any framework SDK — everything here uses
contextweaver core only, so it doubles as a runtime-agnostic skeleton you
can paste into a LangChain / LlamaIndex / OpenAI / homegrown loop.

Run standalone::

    python examples/cookbook/byot_recipe.py

Or via the project test suite::

    make example
"""

from __future__ import annotations

from collections.abc import Callable

from contextweaver.context.manager import ContextManager
from contextweaver.routing.catalog import Catalog
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import ContextItem, ItemKind, Phase, SelectableItem


def web_search(query: str) -> str:
    """Search the public web for *query* and return a short text summary."""
    return f"web_search({query!r}) -> 3 results: example.com, docs.example.com, blog.example.com"


def db_query(sql: str) -> str:
    """Execute a read-only SQL query and return the result rows as JSON."""
    return f"db_query({sql!r}) -> 2 rows"


def send_email(to: str, subject: str, body: str) -> str:
    """Send an email to *to* with *subject* and *body*."""
    return f"send_email(to={to!r}, subject={subject!r}) -> ok"


def schedule_meeting(participants: str, when: str) -> str:
    """Schedule a calendar meeting between *participants* at *when*."""
    return f"schedule_meeting(participants={participants!r}, when={when!r}) -> ok"


TOOLS: dict[str, Callable[..., str]] = {
    "web_search": web_search,
    "db_query": db_query,
    "send_email": send_email,
    "schedule_meeting": schedule_meeting,
}


def _selectable_from_callable(name: str, fn: Callable[..., str]) -> SelectableItem:
    """Wrap a plain Python callable as a routable ``SelectableItem``.

    Args:
        name: The tool ID (also used as ``namespace`` here for brevity).
        fn: The callable to wrap.  Its docstring becomes the tool description.

    Returns:
        A fully populated ``SelectableItem`` ready to register in a ``Catalog``.
    """
    description = (fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else name
    return SelectableItem(
        id=name,
        kind="tool",
        name=name,
        description=description,
        namespace=name.split("_", 1)[0],
        tags=[name.split("_", 1)[0]],
    )


def main() -> None:
    """Run the bring-your-own-tools recipe end-to-end."""
    print("=" * 70)
    print("contextweaver -- Bring-Your-Own-Tools cookbook recipe")
    print("=" * 70)

    # 1. Build a Catalog of SelectableItems from plain Python callables.
    catalog = Catalog()
    for tool_id, fn in TOOLS.items():
        catalog.register(_selectable_from_callable(tool_id, fn))
    print(f"\n[1] Registered {len(catalog.all())} tools in the Catalog.")

    # 2. Build a bounded-choice DAG (small catalog → small graph).
    graph = TreeBuilder(max_children=4).build(catalog.all())
    router = Router(graph, items=catalog.all(), beam_width=2, top_k=2)
    print(f"[2] Built routing graph with {graph.stats()['total_nodes']} nodes.")

    # 3. Route a user query through the graph; the LLM sees a shortlist, not
    # the whole catalog.
    query = "send a follow-up email to alice@example.com about the project"
    result = router.route(query)
    print(f"\n[3] Query: {query!r}")
    print(f"    Routed to {len(result.candidate_ids)} tool(s):")
    for tid, score in zip(result.candidate_ids, result.scores, strict=False):
        print(f"      {tid:20s}  score={score:.3f}")

    # 4. The runtime — that's you — picks one and executes it.  contextweaver
    # never calls the tool; it just narrowed the choice.
    chosen = result.candidate_ids[0]
    raw_output = (
        TOOLS[chosen](
            to="alice@example.com",
            subject="Project follow-up",
            body="Quick check-in on the milestone.",
        )
        if chosen == "send_email"
        else TOOLS[chosen]("project follow-up alice")
    )
    print(f"\n[4] Executed {chosen!r}; raw output: {raw_output}")

    # 5. Feed the result back through the firewall so future builds see a
    # summary rather than the raw bytes (the firewall_threshold here is low
    # purely so the demo demonstrates interception even on tiny outputs).
    mgr = ContextManager()
    mgr.ingest_sync(
        ContextItem(id="u1", kind=ItemKind.user_turn, text=query),
    )
    mgr.ingest_sync(
        ContextItem(id="tc1", kind=ItemKind.tool_call, text=f"{chosen}(...)", parent_id="u1"),
    )
    item, envelope = mgr.ingest_tool_result_sync(
        tool_call_id="tc1",
        raw_output=raw_output,
        tool_name=chosen,
        firewall_threshold=20,
    )
    print(f"\n[5] Firewalled tool result; artifact_ref={item.artifact_ref!r}")
    print(f"    Envelope status: {envelope.status}")

    # 6. Compile a phase-specific prompt for the answer phase.
    pack = mgr.build_sync(phase=Phase.answer, query=query)
    print(
        f"\n[6] Built answer-phase prompt ({len(pack.prompt)} chars, "
        f"{pack.stats.included_count} items included, "
        f"{pack.stats.dropped_count} dropped)."
    )
    print("    Send pack.prompt to your LLM of choice.")


if __name__ == "__main__":
    main()
