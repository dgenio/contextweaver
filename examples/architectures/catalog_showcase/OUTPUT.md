# Catalog showcase — captured run

Output of `python examples/architectures/catalog_showcase/main.py` from a
clean checkout. Deterministic: the catalog is seed-generated, the tool
result is canned, and the firewall is hash-based on artifact content.
Per-section token counts depend on the active tokeniser (tiktoken when
available, otherwise a chars/4 fallback) and may differ slightly across
environments; every other number is character- or count-based and stable.

```

============================================================================
contextweaver -- Catalog showcase reference architecture
============================================================================
Loaded catalog: 65 tools across 9 namespaces

============================================================================
1. Route -> compact shortlist
============================================================================
request:  search the product catalog for 4k monitors under 400 dollars and show the top matches
shortlist: 5 of 65 tools -> ['commerce.product_search', 'commerce.product_details', 'commerce.inventory_check', 'search.internal.search', 'search.web.summarize']

ChoiceCards the model sees (NO argument schemas):
[1/5] commerce.product_search (tool) — Search the product catalog by keyword, price range, and category; returns the top ranked matching products with price and rating [catalog, price, products, search] score=2.30
[2/5] commerce.product_details (tool) — Fetch the full product detail record for a single catalog item by id [catalog, detail, products] score=1.97
[3/5] commerce.inventory_check (tool) — Check warehouse stock levels for a product across regions [inventory, products, stock] score=1.65
[4/5] search.internal.search (tool) — Search internal knowledge base [internal, search] score=0.83
[5/5] search.web.summarize (tool) — Summarize a web page [search, web] score=0.83

============================================================================
2. Expand only the selected tool
============================================================================
chosen:   commerce.product_search  (intent='commerce.product_search', in shortlist)
hydrated schema for 'commerce.product_search': 653 chars
hydrated schema for the other 64 tools: 0 chars (never paid for)

============================================================================
3. Firewall a large tool result
============================================================================
firewall: 3,128 chars -> 501-char summary (artifact artifact:result:tc1)
prompt-side reduction: 84.0%
artifacts kept (addressable for drilldown): 1

============================================================================
4. Final answer-phase prompt
============================================================================
[USER]
search the product catalog for 4k monitors under 400 dollars and show the top matches

[TOOL RESULT [artifact:result:tc1]]
{
  "query": "4k monitors",
  "max_price": 400,
  "total_matches": 15,
  "results": [
    {
      "product_id": "SKU-1000",
      "name": "Acer 27\" 4K UHD Monitor",
      "price_usd": 219,
      "rating": 3.5,
      "in_stock": false,
      "panel": "IPS",
      "refresh_hz": 60
    },
    {
      "product_id": "SKU-1001",
      "name": "Dell 28\" 4K UHD Monitor",
      "price_usd": 226,
      "rating": 4.8,
      "in_stock": true,
      "panel": "VA",
      "refresh_hz": 75
    },
    {
      …

[TOOL CALL]
commerce.product_search(...)

--- BuildStats ---
total_candidates:    3
included_count:      3
dropped_count:       0
dedup_removed:       0
dependency_closures: 0
tokens_per_section:  {'user_turn': 21, 'tool_result': 125, 'tool_call': 7}

============================================================================
Adoption scoreboard
============================================================================
catalog size:           65 tools
shown to model (route): 5 ChoiceCards, 0 schemas
schemas hydrated:       1 (only the selected tool)
large result inlined:   0 bytes (firewalled to a 501-char summary)
```
