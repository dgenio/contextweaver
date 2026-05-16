# Code-review bot

> Production reference architecture for a pull-request review bot fronting
> ~24 analysis tools. Demonstrates how the **context firewall** carries
> the weight of a code-review workflow: large diff and grep outputs go
> straight to the artifact store while the prompt stays compact.

## TL;DR

| What | Where |
|---|---|
| The script | [`examples/architectures/code_review_bot/main.py`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/code_review_bot/main.py) |
| The catalog | [`examples/architectures/code_review_bot/catalog.yaml`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/code_review_bot/catalog.yaml) |
| Captured output | [`examples/architectures/code_review_bot/OUTPUT.md`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/code_review_bot/OUTPUT.md) |
| Local README | [`examples/architectures/code_review_bot/README.md`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/code_review_bot/README.md) |

Run it:

```bash
python examples/architectures/code_review_bot/main.py
```

(Or `make architectures` / `make example`.)

## The shape

The bot walks a six-step review of a regression-introducing refactor in
`payments/charge.py`:

1. *"show me the diff of this pull request against main"* — `git.diff` (large output → firewall)
2. *"grep for the symbol legacy_charge in the codebase"* — `grep.symbol` (large output → firewall)
3. *"run the test suite for the changed module"* — `test.run_module`
4. *"run mypy on the changed module to surface type errors"* — `typecheck.module`
5. *"run ruff on the changed files and report style violations"* — `lint.run`
6. *"post a review comment requesting changes on the regression"* — `review.post_comment`

For each step:

- The [`Router`](../architecture.md#routing-engine) narrows 24 tools to
  a top-3 shortlist (`top_k=3`).
- The bot picks one tool *from the shortlist* using an explicit intent
  map. That separation is the **load-bearing pattern**: contextweaver
  bounds the choice, the bot (or, in production, an LLM) makes the final
  selection.
- The tool is "called" against a mocked backend. Large outputs go
  through the [firewall](../architecture.md#context-firewall) — the
  28 KB diff dump and 2.5 KB grep result become 500-char summaries on
  the prompt while the raw bytes are parked in the artifact store.
- Persistent [facts](../concepts.md) (`pr.target_file`,
  `pr.test_status`, `pr.type_errors`) are written via
  `ContextManager.add_fact_sync` so they survive into the answer-phase
  prompt for every subsequent step.

## What's load-bearing

| contextweaver feature | Used | What it does here |
|---|---|---|
| `Router.route(query)` | ✅ | Narrows 24 tools → top-3 shortlist (`top_k=3`) |
| Bounded choice pattern | ✅ | Bot picks from the shortlist, not from the whole catalog |
| `TreeBuilder` DAG | ✅ | One-shot graph build at startup; routes are sub-millisecond |
| **Context firewall** | ✅✅ | Compacts the ~28 KB diff dump and ~2.5 KB grep result down to ~500-char summaries before they touch the prompt |
| Artifact store | ✅ | Raw bytes stay addressable for drilldown; only summaries land in the prompt |
| Persistent facts | ✅ | Three fact keys survive across all six review steps |
| Tight per-phase budgets | ✅ | `ContextBudget(route=1500, call=2500, interpret=2500, answer=3500)` keeps the answer prompt small even after the firewall externalises the heavy bytes |

## Why this architecture matters

Code-review bots are firewall-bound. Every step produces output that
would saturate a model's context window if inlined: a typical PR diff is
10–50 KB, a grep for a renamed symbol returns dozens of hits, lint and
typecheck pipelines emit hundreds of lines on a hot patch. Without a
firewall, the prompt blows the budget by step 3 and the bot starts
truncating mid-review.

contextweaver's [Context Engine](../architecture.md#context-engine)
handles this without a per-tool integration: the firewall fires on any
result exceeding `firewall_threshold` (2 KB default), parks the raw
bytes in the artifact store, injects a compact summary on the prompt,
and leaves the artifact handle so the LLM can request a drilldown when
needed.

## What's intentionally not here

- **Real git integration.** Mock tool responses keep the example
  deterministic and CI-friendly. A real deployment would wire `git.diff`
  to `subprocess.run(["git", "diff", "main"], ...)` or to an MCP server.
- **LLM-based diff summarisation.** The `review.summarize_diff` tool's
  canned response is a stand-in for what a `Summarizer` plugin
  ([issue #26](https://github.com/dgenio/contextweaver/issues/26))
  would do.
- **Multi-PR session state.** The fact store survives the in-process
  review, but to persist across PRs you would swap the default
  `InMemoryFactStore` for a `SqliteFactStore`
  ([issue #174](https://github.com/dgenio/contextweaver/issues/174)) or
  a Mem0 / Zep adapter
  ([issue #195](https://github.com/dgenio/contextweaver/issues/195)).

## Read next

- [Slack ops bot](slack_ops_bot.md) — the first architecture in the
  series; demonstrates persistent facts and routing across many
  namespaces.
- [Voice agent](voice_agent.md) — the third architecture; demonstrates
  the `asyncio.to_thread(mgr.build_sync, …)` pattern for real-time
  pipelines.
- The [cookbook](../cookbook.md) covers the individual primitives —
  routing, firewall, drilldown — used here.
