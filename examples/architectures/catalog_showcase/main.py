"""Catalog showcase — large-catalog adoption story (#330).

The smallest end-to-end demonstration of the contextweaver value
proposition for a tool-heavy agent:

1. Load a **large** synthetic tool catalog (65 tools).
2. Build a **compact shortlist** for a single realistic user request — the
   model only ever sees a handful of :class:`ChoiceCard` objects, never the
   full catalog and never any argument schema.
3. **Expand only the selected tool** after the shortlist is chosen — its
   full ``args_schema`` is hydrated on demand; the other 64 tools cost
   zero schema bytes.
4. Ingest a **large** (checked-in, synthetic) tool result and let the
   context firewall represent it compactly while the raw bytes stay
   addressable in the artifact store.
5. Print the final answer-phase context pack and :class:`BuildStats`.

Everything is deterministic — the catalog is generated from a fixed seed,
the tool result is canned, and no language model or network call happens.
The point is to show, in one read, why a naive "dump every tool schema and
every raw result into the prompt" loop falls over at catalog scale and how
contextweaver keeps the prompt bounded instead.

Run standalone::

    python examples/architectures/catalog_showcase/main.py

Or via ``make example`` (the ``architectures`` umbrella target).
"""

from __future__ import annotations

import functools
import json

from contextweaver.config import ContextBudget
from contextweaver.context.manager import ContextManager
from contextweaver.routing.cards import make_choice_cards, render_cards_text
from contextweaver.routing.catalog import Catalog, generate_sample_catalog
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import ContextItem, ItemKind, Phase, SelectableItem

# A single realistic request for a commerce ops agent. The interesting tool
# (``commerce.product_search``) carries a real argument schema so the
# "expand only the selected tool" step has something concrete to hydrate.
USER_REQUEST = (
    "search the product catalog for 4k monitors under 400 dollars and show the top matches"
)
INTENT = "commerce.product_search"

# Hand-crafted "hero" tools with populated ``args_schema``. The bulk of the
# catalog is generated synthetically (see ``_build_catalog``) to create
# realistic routing pressure; these few carry full schemas so hydration
# shows a non-trivial payload that the route phase never had to pay for.
_HERO_TOOLS: list[SelectableItem] = [
    SelectableItem(
        id="commerce.product_search",
        kind="tool",
        name="product_search",
        description=(
            "Search the product catalog by keyword, price range, and category; "
            "returns the top ranked matching products with price and rating"
        ),
        tags=["search", "catalog", "products", "price"],
        namespace="commerce",
        args_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Free-text product query"},
                "max_price": {"type": "number", "description": "Maximum unit price filter"},
                "category": {"type": "string", "description": "Optional category slug"},
                "sort": {
                    "type": "string",
                    "enum": ["relevance", "price_asc", "price_desc", "rating"],
                    "default": "relevance",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
            "required": ["query"],
        },
        side_effects=False,
        cost_hint=0.15,
    ),
    SelectableItem(
        id="commerce.product_details",
        kind="tool",
        name="product_details",
        description="Fetch the full product detail record for a single catalog item by id",
        tags=["catalog", "products", "detail"],
        namespace="commerce",
        args_schema={
            "type": "object",
            "properties": {"product_id": {"type": "string"}},
            "required": ["product_id"],
        },
        cost_hint=0.10,
    ),
    SelectableItem(
        id="commerce.inventory_check",
        kind="tool",
        name="inventory_check",
        description="Check warehouse stock levels for a product across regions",
        tags=["inventory", "stock", "products"],
        namespace="commerce",
        cost_hint=0.10,
    ),
]


@functools.cache
def _large_search_result() -> str:
    """Return a ~6 KB synthetic product-search payload (> firewall threshold)."""
    products = [
        {
            "product_id": f"SKU-{1000 + i}",
            "name": f'{brand} {size}" 4K UHD Monitor',
            "price_usd": 219 + (i * 7) % 180,
            "rating": round(3.5 + ((i * 13) % 15) / 10, 1),
            "in_stock": i % 4 != 0,
            "panel": ["IPS", "VA", "OLED"][i % 3],
            "refresh_hz": [60, 75, 120, 144][i % 4],
        }
        for i, (brand, size) in enumerate(
            [
                ("Acer", 27),
                ("Dell", 28),
                ("LG", 27),
                ("Samsung", 32),
                ("ASUS", 28),
                ("BenQ", 27),
                ("HP", 27),
                ("ViewSonic", 32),
                ("MSI", 27),
                ("Gigabyte", 28),
                ("Philips", 27),
                ("AOC", 27),
                ("Lenovo", 28),
                ("Sony", 27),
                ("Apple", 27),
            ]
        )
    ]
    return json.dumps(
        {
            "query": "4k monitors",
            "max_price": 400,
            "total_matches": len(products),
            "results": products,
        },
        indent=2,
    )


def _build_catalog() -> Catalog:
    """Generate a 65-tool catalog: synthetic bulk + a few schema-rich hero tools."""
    catalog = Catalog()
    for raw in generate_sample_catalog(n=62, seed=7):
        catalog.register(SelectableItem.from_dict(raw))
    for hero in _HERO_TOOLS:
        catalog.register(hero)
    return catalog


def _print_header(title: str) -> None:
    """Print a section banner consistent with the other architecture scripts."""
    print()
    print("=" * 76)
    print(title)
    print("=" * 76)


def _select_from_shortlist(shortlist: list[str], intent: str) -> str:
    """Pick *intent* if it is in *shortlist*, else fall back to ``shortlist[0]``.

    The Router bounds the choice to a handful of cards; a real bot (or an LLM
    holding the shortlist in its prompt) makes the final pick. The intent map
    keeps this example deterministic without an LLM.
    """
    if intent in shortlist:
        return intent
    return shortlist[0]


def main() -> None:
    """Run the catalog showcase end-to-end."""
    _print_header("contextweaver -- Catalog showcase reference architecture")

    catalog = _build_catalog()
    items = catalog.all()
    ns_count = len({it.namespace for it in items})
    print(f"Loaded catalog: {len(items)} tools across {ns_count} namespaces")

    # ------------------------------------------------------------------
    # 1. Route the request to a compact shortlist (never the full catalog).
    # ------------------------------------------------------------------
    _print_header("1. Route -> compact shortlist")
    graph = TreeBuilder(max_children=12).build(items)
    router = Router(graph, items=items, beam_width=3, top_k=5)
    result = router.route(USER_REQUEST)
    shortlist = result.candidate_ids
    print(f"request:  {USER_REQUEST}")
    print(f"shortlist: {len(shortlist)} of {len(items)} tools -> {shortlist}")
    cards = make_choice_cards(
        result.candidate_items,
        scores=dict(zip(result.candidate_ids, result.scores, strict=False)),
    )
    print("\nChoiceCards the model sees (NO argument schemas):")
    print(render_cards_text(cards))

    # ------------------------------------------------------------------
    # 2. Expand ONLY the selected tool — hydrate its schema on demand.
    # ------------------------------------------------------------------
    _print_header("2. Expand only the selected tool")
    chosen = _select_from_shortlist(shortlist, INTENT)
    intent_in_shortlist = INTENT in shortlist
    print(
        f"chosen:   {chosen}  "
        f"(intent={INTENT!r}, {'in shortlist' if intent_in_shortlist else 'NOT in shortlist'})"
    )
    hydrated = catalog.hydrate(chosen)
    schema_json = json.dumps(hydrated.args_schema, indent=2, sort_keys=True)
    print(f"hydrated schema for {chosen!r}: {len(schema_json)} chars")
    print(f"hydrated schema for the other {len(items) - 1} tools: 0 chars (never paid for)")

    # ------------------------------------------------------------------
    # 3. Ingest a large tool result; the firewall keeps the prompt compact.
    # ------------------------------------------------------------------
    _print_header("3. Firewall a large tool result")
    budget = ContextBudget(route=1500, call=2500, interpret=2500, answer=3000)
    mgr = ContextManager(budget=budget)
    mgr.ingest_sync(ContextItem(id="u1", kind=ItemKind.user_turn, text=USER_REQUEST))
    mgr.ingest_sync(
        ContextItem(id="tc1", kind=ItemKind.tool_call, text=f"{chosen}(...)", parent_id="u1")
    )
    raw_output = _large_search_result()
    item, _envelope = mgr.ingest_tool_result_sync(
        tool_call_id="tc1",
        raw_output=raw_output,
        tool_name=chosen,
        firewall_threshold=2000,
    )
    handle = item.artifact_ref.handle if item.artifact_ref else "<none>"
    saving = 100.0 * (1.0 - len(item.text) / max(len(raw_output), 1))
    print(
        f"firewall: {len(raw_output):,} chars -> {len(item.text):,}-char summary "
        f"(artifact {handle})"
    )
    print(f"prompt-side reduction: {saving:.1f}%")
    print(
        f"artifacts kept (addressable for drilldown): {len(list(mgr.artifact_store.list_refs()))}"
    )

    # ------------------------------------------------------------------
    # 4. Final answer-phase context pack + BuildStats.
    # ------------------------------------------------------------------
    _print_header("4. Final answer-phase prompt")
    final = mgr.build_sync(phase=Phase.answer, query=USER_REQUEST)
    print(final.prompt)
    print()
    print("--- BuildStats ---")
    print(f"total_candidates:    {final.stats.total_candidates}")
    print(f"included_count:      {final.stats.included_count}")
    print(f"dropped_count:       {final.stats.dropped_count}")
    print(f"dedup_removed:       {final.stats.dedup_removed}")
    print(f"dependency_closures: {final.stats.dependency_closures}")
    print(f"tokens_per_section:  {final.stats.tokens_per_section}")

    _print_header("Adoption scoreboard")
    print(f"catalog size:           {len(items)} tools")
    print(f"shown to model (route): {len(shortlist)} ChoiceCards, 0 schemas")
    print("schemas hydrated:       1 (only the selected tool)")
    print(f"large result inlined:   0 bytes (firewalled to a {len(item.text):,}-char summary)")


if __name__ == "__main__":
    main()
