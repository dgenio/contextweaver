# Code-review bot — reference architecture

> A pull-request review bot fronting ~24 analysis tools. Demonstrates how
> the **context firewall** carries the weight of a code-review workflow:
> diff dumps and grep output are large, so the firewall and artifact
> store are heavily exercised while the prompt stays compact.

## Run it

```bash
python examples/architectures/code_review_bot/main.py
```

(Or `make architectures` / `make example`.)

A captured run of the script lives in [`OUTPUT.md`](OUTPUT.md).

## What this is (and isn't)

This is a **reference architecture**, not a tutorial recipe. The cookbook
gives you copy-paste snippets for individual primitives (routing,
firewall, drilldown, BYO tools); the architecture wires them together
around a realistic problem shape so you can see how they interact.

It is **mocked**: tool implementations return canned strings, no real
git / linter / type checker is invoked. The point is to demonstrate the
contextweaver glue around a code-review-shaped transcript, not to
integrate with a code host.

## Setup

The 24-tool catalog lives in [`catalog.yaml`](catalog.yaml). Loading it:

```python
from contextweaver.routing.catalog import Catalog, load_catalog_yaml

catalog = Catalog()
for item in load_catalog_yaml("catalog.yaml"):
    catalog.register(item)
```

Namespaces: `grep`, `git`, `lint`, `typecheck`, `test`, `review`. Several
tools have side effects (`lint.fix`, `review.post_comment`,
`review.approve`, …); the catalog records this on each
`SelectableItem.side_effects` so a real deployment could refuse to call
them automatically (`Router.route(..., exclude_tags=...)`).

## The review

The bot walks a six-step review of a refactor that introduces a
regression in `payments/charge.py`:

1. *"show me the diff of this pull request against main"* — `git.diff` (large output → firewall)
2. *"grep for the symbol legacy_charge in the codebase"* — `grep.symbol` (large output → firewall)
3. *"run the test suite for the changed module"* — `test.run_module`
4. *"run mypy on the changed module to surface type errors"* — `typecheck.module`
5. *"run ruff on the changed files and report style violations"* — `lint.run`
6. *"post a review comment requesting changes on the regression"* — `review.post_comment`

See `TRANSCRIPT` in [`main.py`](main.py) for the exact text.

## What's load-bearing

| contextweaver feature | Used | What it does here |
|---|---|---|
| `Router.route(query)` | ✅ | Narrows 24 tools → top-3 shortlist (`top_k=3`) |
| Bounded choice pattern | ✅ | Bot picks from the shortlist, not from the whole catalog |
| `TreeBuilder` DAG | ✅ | One-shot graph build at startup; routes are sub-millisecond |
| **Context firewall** | ✅✅ | Compacts the ~28 KB diff dump and ~6 KB grep result down to ~500-char summaries before they touch the prompt |
| Artifact store | ✅ | Raw bytes stay addressable for drilldown; only summaries land in the prompt |
| Persistent facts | ✅ | `pr.target_file`, `pr.test_status`, `pr.type_errors` survive across all six steps |
| Tight per-phase budgets | ✅ | `ContextBudget(route=1500, call=2500, interpret=2500, answer=3500)` keeps the answer prompt small even after the firewall externalises the heavy bytes |

## What's intentionally not here

- **Real git integration.** Mock tool responses keep the example
  deterministic and CI-friendly. A real deployment would wire `git.diff`
  to `subprocess.run(["git", "diff", "main"], ...)` or to an MCP server.
- **LLM-based diff summarisation.** The `review.summarize_diff` tool's
  canned response is a stand-in for what a `Summarizer` plugin (issue
  #26) would do.
- **Multi-PR session state.** The fact store survives the in-process
  review, but to persist across PRs you would swap the default
  `InMemoryFactStore` for a `SqliteFactStore` (issue #174) or a Mem0 /
  Zep adapter (issue #195).

## Read next

- The [cookbook](../../../docs/cookbook.md) covers the individual
  primitives — routing, firewall, drilldown — used here.
- The [architecture overview](../../../docs/architecture.md) walks the
  full Context Engine + Routing Engine pipelines.
- [`docs/architectures/code_review_bot.md`](../../../docs/architectures/code_review_bot.md)
  is the public-docs version of this README.
