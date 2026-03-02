# contextweaver

> Dynamic context management for tool-using AI agents.

contextweaver solves the **context window problem**: as tool catalogs grow and
conversations accumulate history, naive concatenation blows past token limits.
contextweaver provides **phase-specific budgeted context compilation**, a
**context firewall** for large tool outputs, **result envelopes** with
structured fact extraction, and **bounded-choice routing** over large tool
catalogs via DAG + beam search.

## Features

- **Context Engine** — seven-stage pipeline that compiles a phase-aware,
  budget-constrained prompt from the event log.
- **Context Firewall** — intercepts large tool outputs, stores raw data
  out-of-band, and injects compact summaries.
- **Routing Engine** — navigates catalogs of 100+ tools via a bounded DAG
  so the LLM only sees a focused shortlist.
- **Protocol Adapters** — first-class adapters for MCP and A2A protocols.
- **Zero Dependencies** — pure Python ≥ 3.10, stdlib only.
- **Deterministic** — identical inputs always produce identical outputs.

## Installation

```bash
pip install contextweaver
```

Or install from source:

```bash
git clone https://github.com/your-org/contextweaver.git
cd contextweaver
pip install -e ".[dev]"
```

## Quick start

```python
from contextweaver.context.manager import ContextManager
from contextweaver.types import ContextItem, ItemKind, Phase

mgr = ContextManager()
mgr.ingest(ContextItem(id="u1", kind=ItemKind.user_turn, text="How many users?"))
mgr.ingest(ContextItem(id="tc1", kind=ItemKind.tool_call, text="db_query('SELECT COUNT(*) FROM users')", parent_id="u1"))
mgr.ingest(ContextItem(id="tr1", kind=ItemKind.tool_result, text="count: 1042", parent_id="tc1"))

pack = mgr.build_sync(phase=Phase.answer, query="user count")
print(pack.prompt)       # budget-aware compiled context
print(pack.stats)        # what was kept, dropped, deduplicated
```

## Routing large tool catalogs

```python
from contextweaver.routing.catalog import Catalog, load_catalog_json
from contextweaver.routing.tree import TreeBuilder
from contextweaver.routing.router import Router

items = load_catalog_json("catalog.json")
catalog = Catalog()
for item in items:
    catalog.register(item)

graph = TreeBuilder(max_children=10).build(catalog.all())
router = Router(graph, items=catalog.all(), beam_width=3, top_k=5)
result = router.route("send a reminder email about unpaid invoices")
print(result.candidate_ids)
```

## CLI

contextweaver ships with a CLI for quick experimentation:

```bash
contextweaver demo                          # end-to-end demonstration
contextweaver init                          # scaffold config + sample catalog
contextweaver build --catalog c.json --out g.json  # build routing graph
contextweaver route --graph g.json --query "send email"
contextweaver print-tree --graph g.json
contextweaver ingest --events session.jsonl --out session.json
contextweaver replay --session session.json --phase answer
```

## Examples

| Script | Description |
|---|---|
| `minimal_loop.py` | Basic event ingestion → context build |
| `tool_wrapping.py` | Context firewall in action |
| `routing_demo.py` | Build catalog → route queries → choice cards |
| `before_after.py` | Side-by-side token comparison: WITHOUT vs WITH contextweaver |
| `mcp_adapter_demo.py` | MCP adapter: tool conversion, session loading, firewall |
| `a2a_adapter_demo.py` | A2A adapter: agent cards, multi-agent sessions |

Run all examples:

```bash
make example
```

## Documentation

- [Architecture](docs/architecture.md) — package layout, pipeline stages, design principles
- [Concepts](docs/concepts.md) — ContextItem, phases, firewall, ChoiceGraph, etc.
- [MCP Integration](docs/integration_mcp.md) — adapter functions, JSONL format, end-to-end example
- [A2A Integration](docs/integration_a2a.md) — adapter functions, multi-agent sessions

## Development

```bash
make fmt      # format (ruff)
make lint     # lint (ruff)
make type     # type-check (mypy)
make test     # run tests (pytest)
make example  # run all examples
make demo     # run the built-in demo
make ci       # all of the above
```

## License

Apache-2.0
