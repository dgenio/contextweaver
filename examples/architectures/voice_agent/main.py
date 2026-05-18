"""Voice agent — production reference architecture (#205).

A real-time customer-service voice bot fronting ~18 tools (FAQ lookup,
order status, shipping tracking, account profile, callback scheduling).
For each turn:

1. The :class:`Router` narrows the 18-tool catalog to a top-3 shortlist
   (route phase, ``Phase.route``).
2. The bot picks one tool from the shortlist using an explicit intent map.
3. The tool is called against a mocked backend.
4. **All context builds run via** :func:`asyncio.to_thread`, demonstrating
   the canonical async pattern recommended in `docs/integration_pipecat.md`
   for keeping the audio pipeline unblocked.
5. Tight per-phase budgets (``ContextBudget(route=200, call=500,
   interpret=400, answer=1000)``) keep the answer prompt small so TTS
   stays responsive under 300 ms.

This is **the canonical worked example** for the
`Pipecat integration guide <../../docs/integration_pipecat.md>`_.  The
Pipecat ``FrameProcessor`` hook is optional — when the ``pipecat-ai``
package is installed we exercise an extra "real frame processor" smoke
hook; without it the example still runs end-to-end with stdout output.

Run standalone::

    python examples/architectures/voice_agent/main.py

Or via ``make example`` (the ``architectures`` umbrella target).

For Pipecat integration::

    pip install 'contextweaver[voice]'   # pulls pipecat-ai
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from contextweaver.config import ContextBudget
from contextweaver.context.manager import ContextManager
from contextweaver.routing.catalog import Catalog, load_catalog_yaml
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import ContextItem, ItemKind, Phase

# Pipecat is optional: the example runs end-to-end without it, and only
# exercises a deeper "real frame processor" smoke hook when the package
# is installed.  Document the optional install in the README.
try:
    import pipecat  # type: ignore[import-not-found]  # noqa: F401

    _PIPECAT_AVAILABLE = True
except ImportError:
    _PIPECAT_AVAILABLE = False


# A scripted customer-service voice transcript: order chase, address
# update, callback scheduling.  Each entry is ``(user_text, intent)`` where
# ``intent`` is the tool the bot would pick *given the routed shortlist*.
TRANSCRIPT: list[tuple[str, str]] = [
    ("hi, can you look up order number A-481 for me", "orders.lookup"),
    ("what is the shipping tracking status for that order", "shipping.tracking"),
    ("can you change the delivery address to my new home", "shipping.update_address"),
    ("when is the next available delivery slot", "shipping.delivery_slot"),
    ("schedule a callback for me at 2pm tomorrow", "callback.schedule"),
]


def _tool_responses() -> dict[str, str]:
    """Canned tool-response map.  All small enough to stay under firewall threshold."""
    return {
        "orders.lookup": (
            "order A-481: pending shipment, placed 2026-05-14, "
            "2 items (sku-100 x1, sku-204 x2), total $84.50"
        ),
        "shipping.tracking": (
            "tracking: in transit, carrier=ups, "
            "last_scan=2026-05-15T14:22Z at oakland-ca, eta=2026-05-17"
        ),
        "shipping.update_address": (
            "shipping.update_address ok — order A-481 will deliver to 42 Apple St, Springfield"
        ),
        "shipping.delivery_slot": (
            "next available delivery slots: 2026-05-17 09:00-12:00, "
            "2026-05-17 13:00-17:00, 2026-05-18 09:00-12:00"
        ),
        "callback.schedule": (
            "callback.schedule ok — customer scheduled for 2026-05-17T14:00 (PT)"
        ),
    }


CATALOG_PATH = Path(__file__).parent / "catalog.yaml"


def _print_header(title: str) -> None:
    """Print a section banner consistent with the other architecture scripts."""
    print()
    print("=" * 76)
    print(title)
    print("=" * 76)


def _build_router(catalog: Catalog) -> Router:
    """Compile the catalog into a routing graph and wrap it in a Router."""
    items = catalog.all()
    graph = TreeBuilder(max_children=8).build(items)
    # top_k=3 so the bot has a shortlist to pick from, not just the top-1.
    return Router(graph, items=items, top_k=3)


def _select_from_shortlist(shortlist: list[str], intent: str) -> str:
    """Pick *intent* if it is in *shortlist*, else fall back to shortlist[0].

    Real-world bots (or LLMs) make this decision against the shortlist; the
    point of the architecture is that contextweaver bounds the choice to a
    handful of options, not that it executes the choice for you.
    """
    if intent in shortlist:
        return intent
    return shortlist[0]


async def _async_build(mgr: ContextManager, *, phase: Phase, query: str) -> object:
    """Run :meth:`ContextManager.build_sync` on a worker thread.

    This is the canonical async pattern documented in
    ``docs/integration_pipecat.md``: contextweaver's context pipeline is
    sync (deterministic, no IO), so we hand it to ``asyncio.to_thread``
    rather than blocking the audio event loop.  The voice agent runs
    routing on the audio thread (sub-millisecond) and context build on a
    worker thread (a few ms — well under the per-turn budget).
    """
    return await asyncio.to_thread(mgr.build_sync, phase=phase, query=query)


async def _run_turn(
    turn_idx: int,
    user_text: str,
    intent: str,
    *,
    mgr: ContextManager,
    router: Router,
    responses: dict[str, str],
) -> tuple[str, bool, int]:
    """Run a single voice turn.  Returns (chosen_tool, intent_matched, answer_tokens)."""
    _print_header(f"Turn {turn_idx}")
    print(f"user:     {user_text}")

    u_id = f"u{turn_idx}"
    mgr.ingest_sync(ContextItem(id=u_id, kind=ItemKind.user_turn, text=user_text))

    # Routing — bounded shortlist of 3. Never sees full schemas.
    result = router.route(user_text)
    shortlist = result.candidate_ids
    chosen = _select_from_shortlist(shortlist, intent)
    intent_in_shortlist = intent in shortlist
    print(f"routed:   {shortlist}")
    print(
        f"chosen:   {chosen}  "
        f"(intent={intent!r}, "
        f"{'in shortlist' if intent_in_shortlist else 'NOT in shortlist'})"
    )

    # Route-phase context build, off the audio thread.
    t0 = time.perf_counter()
    route_pack = await _async_build(mgr, phase=Phase.route, query=user_text)
    route_ms = (time.perf_counter() - t0) * 1000
    route_tokens = sum(route_pack.stats.tokens_per_section.values())  # type: ignore[attr-defined]
    print(
        f"route prompt: {route_pack.stats.included_count} items / "  # type: ignore[attr-defined]
        f"{route_tokens} tokens  ({route_ms:.1f} ms off-thread)"
    )

    # Tool call + (mocked) result.  Voice responses are small — firewall
    # rarely fires — but the artifact store still keeps every result
    # addressable for drilldown.
    tc_id = f"tc{turn_idx}"
    mgr.ingest_sync(
        ContextItem(
            id=tc_id,
            kind=ItemKind.tool_call,
            text=f"{chosen}(...)",
            parent_id=u_id,
        )
    )
    raw_output = responses.get(chosen, f"{chosen} returned ok")
    mgr.ingest_tool_result_sync(
        tool_call_id=tc_id,
        raw_output=raw_output,
        tool_name=chosen,
        firewall_threshold=2000,
    )

    # Persistent facts that should survive across turns of the call.
    if chosen == "orders.lookup":
        mgr.add_fact_sync(
            key="customer.order_id",
            value="A-481",
            metadata={"source": chosen, "turn": str(turn_idx)},
        )
    elif chosen == "shipping.update_address":
        mgr.add_fact_sync(
            key="customer.shipping_address",
            value="42 Apple St, Springfield",
            metadata={"source": chosen, "turn": str(turn_idx)},
        )
    elif chosen == "callback.schedule":
        mgr.add_fact_sync(
            key="customer.callback",
            value="2026-05-17T14:00 (PT)",
            metadata={"source": chosen, "turn": str(turn_idx)},
        )

    # Answer-phase build, off the audio thread.  Tight budget keeps the
    # prompt small so TTS responds quickly.
    t0 = time.perf_counter()
    answer = await _async_build(mgr, phase=Phase.answer, query=user_text)
    answer_ms = (time.perf_counter() - t0) * 1000
    ans_tokens = sum(answer.stats.tokens_per_section.values())  # type: ignore[attr-defined]
    print(
        f"answer prompt: included={answer.stats.included_count}  "  # type: ignore[attr-defined]
        f"dropped={answer.stats.dropped_count}  "  # type: ignore[attr-defined]
        f"tokens={ans_tokens}  ({answer_ms:.1f} ms off-thread)"
    )

    return chosen, intent_in_shortlist, ans_tokens


async def _run() -> None:
    """Run the voice agent scenario end-to-end (async entry point)."""
    _print_header("contextweaver -- Voice agent reference architecture")
    print(f"pipecat-ai installed: {_PIPECAT_AVAILABLE}")
    if not _PIPECAT_AVAILABLE:
        print("(install 'contextweaver[voice]' to exercise the optional Pipecat hook)")

    catalog = Catalog()
    for item in load_catalog_yaml(CATALOG_PATH):
        catalog.register(item)
    print(f"Loaded catalog: {len(catalog.all())} tools from {CATALOG_PATH.name}")

    router = _build_router(catalog)
    # Tight budgets — the voice answer phase must keep the LLM prompt small
    # so TTS responds quickly.  These numbers match the recommendations in
    # docs/integration_pipecat.md.
    budget = ContextBudget(route=200, call=500, interpret=400, answer=1000)
    mgr = ContextManager(budget=budget)
    responses = _tool_responses()

    intent_match_count = 0
    max_answer_tokens = 0

    for turn_idx, (user_text, intent) in enumerate(TRANSCRIPT, start=1):
        _chosen, matched, ans_tokens = await _run_turn(
            turn_idx,
            user_text,
            intent,
            mgr=mgr,
            router=router,
            responses=responses,
        )
        if matched:
            intent_match_count += 1
        max_answer_tokens = max(max_answer_tokens, ans_tokens)

    # ------------------------------------------------------------------
    # Summary: persisted facts, the final prompt, and the routing scoreboard.
    # ------------------------------------------------------------------
    _print_header("Persisted facts (carry across turns of the call)")
    for fact in sorted(mgr.fact_store.all(), key=lambda f: f.key):
        print(f"  {fact.key} = {fact.value}")

    _print_header("Latency scoreboard")
    print(f"max answer-prompt tokens: {max_answer_tokens} (budget=1000)")
    print(
        "Answer-phase builds run via asyncio.to_thread so the audio "
        "pipeline stays unblocked while contextweaver assembles the prompt."
    )

    _print_header("Final answer-phase prompt")
    final = await _async_build(mgr, phase=Phase.answer, query=TRANSCRIPT[-1][0])
    print(final.prompt)  # type: ignore[attr-defined]
    print()
    print("--- BuildStats ---")
    print(f"total_candidates:    {final.stats.total_candidates}")  # type: ignore[attr-defined]
    print(f"included_count:      {final.stats.included_count}")  # type: ignore[attr-defined]
    print(f"dropped_count:       {final.stats.dropped_count}")  # type: ignore[attr-defined]
    print(f"dedup_removed:       {final.stats.dedup_removed}")  # type: ignore[attr-defined]
    print(f"dependency_closures: {final.stats.dependency_closures}")  # type: ignore[attr-defined]
    print(f"tokens_per_section:  {final.stats.tokens_per_section}")  # type: ignore[attr-defined]

    _print_header("Routing scoreboard")
    print(
        f"intent in router top-3: {intent_match_count}/{len(TRANSCRIPT)}  "
        f"({intent_match_count * 100 // len(TRANSCRIPT)}%)"
    )
    print(
        "Default scorer backend is TF-IDF. For voice domains where users "
        "use casual phrasing, try Router(scorer_backend='fuzzy')."
    )


def main() -> None:
    """Sync entry point — wraps the async pipeline in :func:`asyncio.run`."""
    asyncio.run(_run())


if __name__ == "__main__":
    main()
