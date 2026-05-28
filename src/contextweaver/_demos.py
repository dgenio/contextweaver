"""Built-in scenario implementations for ``contextweaver demo``.

Each ``run_*`` function is a self-contained, deterministic walkthrough of
one part of the library — no network, no LLM, fixed seeds. Wired into the
CLI from :mod:`contextweaver.__main__`. Kept private (leading underscore)
because the CLI is the supported entry point, not these function signatures.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from contextweaver.context.firewall import apply_firewall
from contextweaver.context.manager import ContextManager
from contextweaver.routing.cards import count_tokens, make_choice_cards, render_cards_text
from contextweaver.routing.catalog import Catalog, generate_sample_catalog
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.store.artifacts import InMemoryArtifactStore
from contextweaver.types import ContextItem, ItemKind, Phase, SelectableItem

_BAR = "=" * 60


def _banner(title: str) -> None:
    print(_BAR)
    print(f"contextweaver demo — {title}")
    print(_BAR)


def _footer() -> None:
    print()
    print(_BAR)
    print("Demo complete.")


def run_default() -> None:
    """Friendly end-to-end walkthrough on a small catalog + event log."""
    _banner("default scenario")

    raw_items = generate_sample_catalog(n=40, seed=42)
    catalog = Catalog()
    for raw in raw_items:
        catalog.register(SelectableItem.from_dict(raw))
    items = catalog.all()
    ns_count = len({it.namespace for it in items})
    print(f"\n[1/5] Loaded catalog: {len(items)} items across {ns_count} namespaces")

    builder = TreeBuilder(max_children=10)
    graph = builder.build(items)
    gstats = graph.stats()
    print(f"[2/5] Built routing graph: {gstats['total_nodes']} nodes, depth={gstats['max_depth']}")

    router = Router(graph, items=items, beam_width=3, top_k=5)
    query = "find unpaid invoices and send a reminder email"
    result = router.route(query)
    print(f"[3/5] Routed query: {query!r}")
    print(f"      Top candidates: {result.candidate_ids}")
    cards = make_choice_cards(
        result.candidate_items,
        scores=dict(zip(result.candidate_ids, result.scores, strict=False)),
    )
    print(f"      Choice cards ({len(cards)}):")
    print(render_cards_text(cards))

    mgr = ContextManager()
    mgr.ingest(
        ContextItem(id="u1", kind=ItemKind.user_turn, text="How many open invoices do we have?")
    )
    mgr.ingest(
        ContextItem(id="a1", kind=ItemKind.agent_msg, text="Let me check the billing system.")
    )
    mgr.ingest(
        ContextItem(
            id="tc1",
            kind=ItemKind.tool_call,
            text="invoices.search(status='open')",
            parent_id="u1",
        )
    )
    mgr.ingest(
        ContextItem(
            id="tr1",
            kind=ItemKind.tool_result,
            text=(
                "invoice_id: INV-001\nstatus: open\namount: 5000\n\n"
                "invoice_id: INV-002\nstatus: open\namount: 3200\n\n"
                "summary: 2 open invoices, total $8,200"
            ),
            parent_id="tc1",
        )
    )
    mgr.add_fact("customer_tier", "enterprise")
    mgr.add_episode("ep-prev", "Previously discussed payment terms with client")

    pack = mgr.build_sync(phase=Phase.answer, query="open invoices")
    print(f"\n[4/5] Built context pack: phase={pack.phase.value}")
    print(f"      Candidates: {pack.stats.total_candidates}, Included: {pack.stats.included_count}")
    print(
        f"      Dedup removed: {pack.stats.dedup_removed},"
        f" Closures: {pack.stats.dependency_closures}"
    )
    print(f"      Token breakdown: {pack.stats.tokens_per_section}")

    preview = pack.prompt[:400]
    print(f"\n[5/5] Prompt preview ({len(pack.prompt)} chars total):")
    print(preview)
    if len(pack.prompt) > 400:
        print("      ...")

    _footer()


def _synth_catalog(n: int, seed: int = 42) -> list[SelectableItem]:
    """Build an n-item :class:`SelectableItem` list, extending the 83-item
    sample pool with synthetic variants when ``n > 83``.

    Mirrors ``benchmarks.benchmark._make_catalog`` so the demo's 1,000-tool
    catalog is the same shape as what the benchmark exercises.
    """
    base = [SelectableItem.from_dict(d) for d in generate_sample_catalog(n=n, seed=seed)]
    if n <= len(base):
        return sorted(base, key=lambda i: i.id)[:n]

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


def run_large_catalog() -> None:
    """1,000-tool catalog routed to a handful of compact ChoiceCards."""
    _banner("large-catalog scenario")

    catalog_size = 1000
    beam_width = 3
    top_k = 5

    catalog = Catalog()
    for item in _synth_catalog(catalog_size, seed=42):
        catalog.register(item)
    items = catalog.all()
    ns_count = len({it.namespace for it in items})
    print(f"\nCatalog size:           {len(items)} tools across {ns_count} namespaces")

    builder = TreeBuilder(max_children=20)
    graph = builder.build(items)
    gstats = graph.stats()
    print(f"Routing graph:          {gstats['total_nodes']} nodes, depth={gstats['max_depth']}")
    print(f"Beam width / top_k:     {beam_width} / {top_k}")

    router = Router(graph, items=items, beam_width=beam_width, top_k=top_k)
    query = "create a github issue for an incident"
    result = router.route(query)

    print(f"\nQuery: {query!r}")
    print(f"Cards exposed to model: {len(result.candidate_ids)} of {len(items)}")
    print(f"Selected candidate IDs: {result.candidate_ids}")

    cards = make_choice_cards(
        result.candidate_items,
        scores=dict(zip(result.candidate_ids, result.scores, strict=False)),
    )
    rendered = render_cards_text(cards)
    print(f"\nCard text the model sees ({len(rendered)} chars — note: NO full schemas):")
    print(rendered)

    _footer()


def run_huge_tool_output() -> None:
    """Context firewall demo: a ~10 KB raw tool result becomes a tiny summary."""
    _banner("huge-tool-output scenario")

    rows = [
        f"row_{idx:03d}: customer_id=C-{idx:05d}  "
        f"email=user{idx}@example.com  status={'active' if idx % 3 else 'churned'}  "
        f"mrr={(idx * 137) % 10000}"
        for idx in range(1, 121)
    ]
    raw_text = (
        "status: ok\n"
        f"rows_returned: {len(rows)}\n"
        "execution_time_ms: 248\n\n" + "\n".join(rows) + "\n"
    )

    raw_item = ContextItem(
        id="tr-bigquery",
        kind=ItemKind.tool_result,
        text=raw_text,
        metadata={"tool": "bigquery.customers"},
        parent_id="tc-bigquery",
    )

    print(f"\nRaw tool output:   {len(raw_text)} chars ({len(rows)} rows)")
    print("First 100 chars:")
    print(f"  {raw_text[:100]!r}")

    store = InMemoryArtifactStore()
    processed, envelope = apply_firewall(raw_item, store)

    print("\n--- After context firewall ---")
    print(f"What enters the prompt (item.text): {len(processed.text)} chars")
    print("Prompt-side summary:")
    for line in processed.text.splitlines():
        print(f"  {line}")
    print(f"\nArtifact ref:      {processed.artifact_ref}")
    if envelope:
        print(f"Envelope status:   {envelope.status}")
        print(f"Envelope summary:  {envelope.summary[:120]!r}")
        if envelope.facts:
            print(f"Extracted facts ({len(envelope.facts)}):")
            for fact in envelope.facts[:5]:
                print(f"  - {fact}")

    print("\n--- Artifact store ---")
    for ref in store.list_refs():
        print(f"Handle: {ref.handle}  ({ref.size_bytes} bytes raw)")

    saving = 100.0 * (1.0 - len(processed.text) / max(len(raw_text), 1))
    print(f"\nToken savings vs raw: {saving:.1f}%")
    _footer()


def _pct_reduction(before: str, after: str) -> str:
    """Return a ``NN.N%`` character-reduction string for *before* → *after*."""
    saving = 100.0 * (1.0 - len(after) / max(len(before), 1))
    return f"{saving:.1f}%"


def _killer_history() -> list[ContextItem]:
    """Build a long prior conversation (the kind that floods a naive prompt)."""
    turns: list[ContextItem] = []
    chatter = [
        ("u", "Morning — can you help me chase some overdue accounts today?"),
        ("a", "Of course. I can search billing, read CRM notes, and draft comms."),
        ("u", "Great. Last week we talked about the enterprise tier renewals."),
        ("a", "Right — three accounts were flagged for manual follow-up."),
        ("u", "One of them, Northwind, disputed an invoice. Did that resolve?"),
        ("a", "The dispute was closed; the invoice is valid and still unpaid."),
        ("u", "Also remember the finance team wants reminders to stay polite."),
        ("a", "Noted — I keep a friendly, non-threatening tone on all reminders."),
    ]
    for idx, (who, text) in enumerate(chatter, start=1):
        kind = ItemKind.user_turn if who == "u" else ItemKind.agent_msg
        turns.append(ContextItem(id=f"h{idx}", kind=kind, text=text))
    return turns


def _killer_big_result() -> str:
    """Return a ~12 KB invoice/account-notes dump (floods a naive answer prompt)."""
    rows = [
        f'{{"invoice_id":"INV-{2000 + i}","account":"ACME-{i % 40:03d}",'
        f'"amount_usd":{(i * 317) % 9000 + 100},"days_overdue":{(i * 7) % 95},'
        f'"status":"unpaid","last_note":"left voicemail; awaiting AP confirmation #{i}"}}'
        for i in range(90)
    ]
    return (
        "status: ok\n"
        f"rows_returned: {len(rows)}\n"
        "source: billing.invoices.search\n\n" + "\n".join(rows) + "\n"
    )


def run_killer() -> None:
    """The 60-second failure mode: 100 tools + long history + a huge result (#322).

    A single deterministic, network-free scenario that makes the pain of a
    naive agent loop obvious in under a minute: a 100-tool catalog bloats
    the route prompt, a long conversation competes for budget, and a huge
    tool result floods the answer prompt.  contextweaver narrows the
    catalog to a handful of :class:`ChoiceCard` objects and firewalls the
    big result out-of-band, so the prompt stays bounded.

    Sizes are reported in **characters** (deterministic everywhere); the
    closing token estimate uses the active tokeniser and is informational.
    """
    _banner("killer scenario (100 tools + huge output)")

    catalog = Catalog()
    for item in _synth_catalog(100, seed=42):
        catalog.register(item)
    items = catalog.all()
    query = "Find unpaid invoices, check the account notes, and draft a reminder."
    print(f"\nCatalog: {len(items)} tools across {len({i.namespace for i in items})} namespaces")
    print(f"User:    {query!r}")

    # ---- 1. Tool catalog: naive dump vs ChoiceCard shortlist --------------
    naive_tools = "\n".join(f"- {it.id} ({it.namespace}): {it.description}" for it in items)
    graph = TreeBuilder(max_children=20).build(items)
    router = Router(graph, items=items, beam_width=3, top_k=5)
    result = router.route(query)
    cards = make_choice_cards(
        result.candidate_items,
        scores=dict(zip(result.candidate_ids, result.scores, strict=False)),
    )
    cw_tools = render_cards_text(cards)
    chosen = result.candidate_ids[0]
    print("\n[1/3] Tools in the route prompt")
    print(f"      naive (all {len(items)} tools):        {len(naive_tools):,} chars")
    print(f"      contextweaver ({len(cards)} ChoiceCards):    {len(cw_tools):,} chars")
    print(f"      reduction: {_pct_reduction(naive_tools, cw_tools)}")
    print(f"      shortlist: {result.candidate_ids}")

    # ---- 2. Huge tool output: raw vs firewalled ---------------------------
    mgr = ContextManager()
    for turn in _killer_history():
        mgr.ingest_sync(turn)
    mgr.ingest_sync(ContextItem(id="u-now", kind=ItemKind.user_turn, text=query))
    mgr.ingest_sync(
        ContextItem(
            id="tc1", kind=ItemKind.tool_call, text=f"{chosen}(status='unpaid')", parent_id="u-now"
        )
    )
    big = _killer_big_result()
    result_item, _envelope = mgr.ingest_tool_result_sync(
        tool_call_id="tc1", raw_output=big, tool_name=chosen, firewall_threshold=2000
    )
    handle = result_item.artifact_ref.handle if result_item.artifact_ref else "<none>"
    print("\n[2/3] The huge tool result")
    print(f"      naive (raw):             {len(big):,} chars")
    print(f"      contextweaver (summary):  {len(result_item.text):,} chars  (artifact {handle})")
    print(f"      reduction: {_pct_reduction(big, result_item.text)}")

    # ---- 3. The whole answer prompt: everything raw vs compiled -----------
    raw_history = "\n".join(t.text for t in _killer_history())
    naive_prompt = f"{naive_tools}\n\n{raw_history}\n\n{query}\n\n{big}"
    pack = mgr.build_sync(phase=Phase.answer, query=query)
    print("\n[3/3] The full answer prompt")
    print(f"      naive (everything raw):   {len(naive_prompt):,} chars")
    print(f"      contextweaver (compiled):  {len(pack.prompt):,} chars")
    print(f"      reduction: {_pct_reduction(naive_prompt, pack.prompt)}")

    print(
        f"\nToken estimate: naive ~{count_tokens(naive_prompt):,} tokens "
        f"-> contextweaver ~{count_tokens(pack.prompt):,} tokens"
    )
    _footer()


# --- MCP gateway scenario -------------------------------------------------

_UPSTREAM_DEFS: list[dict[str, Any]] = [
    {
        "name": "github.create_issue",
        "description": "Open a new GitHub issue.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "linear.create_ticket",
        "description": "Create a Linear ticket from a description.",
        "inputSchema": {
            "type": "object",
            "properties": {"description": {"type": "string"}},
            "required": ["description"],
        },
    },
    {
        "name": "bigquery.run_query",
        "description": "Execute a BigQuery SQL query and return rows.",
        "inputSchema": {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
        },
    },
]


async def _stub_handler(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Return a canned MCP-shaped tool result.

    The MCP wire shape is ``{"content": [{"type": "text", "text": ...}], "isError": False}``
    — matching it is what lets :class:`~contextweaver.adapters.ProxyRuntime` pipe the
    response through the context firewall and produce a non-empty summary.
    """
    if name == "github.create_issue":
        body = "\n".join(
            [
                "issue_id: 142",
                f"title: {args.get('title', '<empty>')!r}",
                f"body: {args.get('body', '<empty>')!r}",
                "status: open",
                "html_url: https://github.com/demo/repo/issues/142",
            ]
        )
    elif name == "linear.create_ticket":
        body = f"ticket_id: TKT-123\ndescription: {str(args.get('description', ''))[:60]!r}"
    else:
        body = f"stub called {name} with {sorted(args.keys())}"
    return {"content": [{"type": "text", "text": body}], "isError": False}


def run_mcp_gateway() -> None:
    """End-to-end MCP gateway demo using ProxyRuntime + StubUpstream."""
    asyncio.run(_run_mcp_gateway_async())


def run_mcp_gateway_full() -> None:
    """Full 60-tool MCP Context Gateway architecture run (issue #264).

    Thin re-entry into the reference architecture's ``main()`` so
    ``contextweaver demo --scenario mcp-gateway-full`` reaches the same
    end-to-end narrative as ``python examples/architectures/mcp_context_gateway/main.py``
    without requiring users to clone the repo. The catalog ships inside
    ``contextweaver.data`` so this works from a wheel install too.
    """
    _banner("mcp-gateway-full scenario (60-tool reference architecture)")

    # Late import: the example main.py performs non-trivial work at import
    # time only when ``main()`` is called, but we keep it lazy anyway so
    # the other demo scenarios don't pay the cost on import.
    import importlib.util
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent.parent
    main_path = repo_root / "examples" / "architectures" / "mcp_context_gateway" / "main.py"
    if main_path.is_file():
        # Source-tree / editable install path — load the example verbatim
        # so we exercise the exact code path users will copy-paste from
        # the README.
        spec = importlib.util.spec_from_file_location(
            "contextweaver._demos_mcp_gateway_full", main_path
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(
                f"Cannot load reference architecture from {main_path} "
                "(importlib could not derive a module spec or loader)."
            )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.main()
    else:
        # Wheel-install fallback: re-implement the architecture's flow against
        # the packaged catalog so users on ``pip install contextweaver`` (no
        # examples/) still see the full narrative.
        _run_mcp_gateway_full_packaged()

    _footer()


def _run_mcp_gateway_full_packaged() -> None:
    """Wheel-install fallback for :func:`run_mcp_gateway_full`.

    Mirrors the reference architecture's narrative but loads everything
    from ``contextweaver.data``. Kept inline (rather than importing the
    example) so ``contextweaver demo --scenario mcp-gateway-full`` works
    even when ``examples/`` was stripped from the install.
    """
    from contextweaver.config import ContextBudget
    from contextweaver.data import gateway_catalog_path
    from contextweaver.routing.catalog import Catalog, load_catalog_yaml

    _banner("mcp-gateway-full scenario (packaged catalog)")
    catalog = Catalog()
    for selectable in load_catalog_yaml(gateway_catalog_path()):
        catalog.register(selectable)
    items = catalog.all()
    ns_count = len({it.namespace for it in items})
    print(f"\nLoaded catalog: {len(items)} tools across {ns_count} namespaces")

    routing_query = "Execute a BigQuery query to find MRR delta rows for customer C-12345"
    builder = TreeBuilder(max_children=10)
    graph = builder.build(items)
    router = Router(graph, items=items, top_k=5)
    result = router.route(routing_query)
    cards = make_choice_cards(
        result.candidate_items,
        scores=dict(zip(result.candidate_ids, result.scores, strict=False)),
    )
    print(f"\nRoute: {routing_query!r}")
    print(f"Shortlist: {result.candidate_ids}")
    print(render_cards_text(cards))

    chosen = "bigquery.run_query"
    hydrated = catalog.hydrate(chosen)
    schema_json = json.dumps(hydrated.args_schema, indent=2, sort_keys=True)
    print(f"\nHydrated schema for {chosen!r}: {len(schema_json)} chars")
    print(f"Hydrated schema for the other {len(items) - 1} tools: 0 chars (skipped)")

    mgr = ContextManager(budget=ContextBudget(answer=2000))
    mgr.ingest_sync(ContextItem(id="u1", kind=ItemKind.user_turn, text="Why did MRR drop?"))
    mgr.ingest_sync(
        ContextItem(
            id="tc1",
            kind=ItemKind.tool_call,
            text=f"{chosen}(sql=...)",
            parent_id="u1",
        )
    )
    big_text = "rowset: bigquery.run_query\nrows_returned: 90\n\n" + "\n".join(
        f'{{"day":{d},"mrr_delta_usd":{(137 * d) % 600 - 300}}}' for d in range(1, 91)
    )
    item, envelope = mgr.ingest_mcp_result(
        tool_call_id="tc1",
        mcp_result={"content": [{"type": "text", "text": big_text}], "isError": False},
        tool_name=chosen,
        firewall_threshold=2000,
    )
    artifact_handle = item.artifact_ref.handle if item.artifact_ref else "<none>"
    pack = mgr.build_sync(phase=Phase.answer, query=routing_query)
    saving = 100.0 * (1.0 - len(item.text) / max(len(big_text), 1))

    print(
        f"\nFirewall: {len(big_text):,} chars → {len(item.text):,}-char summary "
        f"(artifact {artifact_handle})"
    )
    print(f"Final prompt tokens: {pack.stats.prompt_tokens}")
    print(f"Firewall reduction: {saving:.1f}%")
    _ = envelope  # consumed via mgr; surfaced for symmetry with the example.
    _footer()


async def _run_mcp_gateway_async() -> None:
    from contextweaver.adapters import (
        ProxyRuntime,
        StubUpstream,
        dispatch_meta_tool,
        make_gateway_meta_tools,
    )

    _banner("mcp-gateway scenario")

    runtime = ProxyRuntime(StubUpstream(_UPSTREAM_DEFS, handler=_stub_handler))
    runtime.register_tool_defs_sync(_UPSTREAM_DEFS)

    print("\n[1/4] Meta-tools the gateway advertises to the agent:")
    for meta in make_gateway_meta_tools(runtime):
        print(f"      - {meta['name']}: {meta['description'][:60]}…")

    print("\n[2/4] tool_browse(query='open a github issue')  ← schemas NOT hydrated yet")
    browse = await dispatch_meta_tool(runtime, "tool_browse", {"query": "open a github issue"})
    cards = json.loads(browse["content"][0]["text"])
    print(f"      {len(cards)} card(s) returned:")
    for card in cards[:3]:
        print(f"        [{card['id']}] {card['description'][:60]}")

    tool_id = next(i for i in runtime.list_tool_ids() if i.startswith("github:create_issue"))
    print(f"\n[3/4] tool_execute({tool_id})  ← schema hydrated, args validated, firewall runs")
    exec_result = await dispatch_meta_tool(
        runtime,
        "tool_execute",
        {"tool_id": tool_id, "args": {"title": "Demo issue", "body": "Hello"}},
    )
    envelope_dict = json.loads(exec_result["content"][0]["text"])
    print(f"      status={envelope_dict['status']}")
    print(f"      summary={envelope_dict['summary'][:80]!r}")

    print("\n[4/4] What lands in the agent's context")
    handles = list(runtime.context_manager.artifact_store.list_refs())
    if handles:
        print(f"      Artifact stored out-of-band: {handles[0].handle}")
        view = await dispatch_meta_tool(
            runtime,
            "tool_view",
            {"handle": handles[0].handle, "selector": {"type": "head", "n_chars": 40}},
        )
        head = view["content"][0]["text"]
        print(f"      Drilldown view (first 40 chars): {head!r}")
    else:
        print("      (no artifact persisted — text-only upstream response)")

    _footer()
