# Contribution Paths

> Where can I help in 30 minutes, half a day, or one weekend?
>
> [`CONTRIBUTING.md`](https://github.com/dgenio/contextweaver/blob/main/CONTRIBUTING.md)
> is the full setup-and-style reference. This page is the shortcut: pick the
> kind of help you want to give and jump straight to the relevant section,
> commands, and issue labels — without reading the whole repo first.

Every path below runs the same validation gate. Confirm your environment is
healthy before you start:

```bash
pip install -e ".[dev]"
make ci        # fmt + lint + type + test + schemas-check + example + demo
```

## Pick your path

| I want to… | Time | Start here | Issue labels |
|---|---|---|---|
| Improve docs | 30 min | [Docs](#i-want-to-improve-docs) | `documentation`, `good first issue` |
| Add an adapter | 1 weekend | [Adapters](#i-want-to-add-an-adapter) | `enhancement`, `area/adapters` |
| Add a benchmark scenario | half a day | [Benchmarks](#i-want-to-add-a-benchmark-scenario) | `area/eval` |
| Improve adoption / growth | half a day | [Adoption](#i-want-to-improve-adoption-growth) | `documentation`, `enhancement` |
| Grab a good first issue | 30 min–½ day | [Good first issue](#i-want-a-good-first-issue) | `good first issue` |
| Drive it with an AI assistant | any | [AI assistant](#i-am-using-an-ai-coding-assistant) | `agent-friendly` |

## I want to improve docs

The lowest-friction way in. Docs live under [`docs/`](https://github.com/dgenio/contextweaver/tree/main/docs)
and the site is built with `make docs` (preview with `make docs-serve` at
<http://127.0.0.1:8000>).

- **README clarity** — tighten [`README.md`](https://github.com/dgenio/contextweaver/blob/main/README.md);
  the first screen is the category pitch.
- **Recipes** — add a copy-paste pattern to the [Cookbook](cookbook.md). Each
  recipe is runnable core-only code, so it does not bitrot.
- **Integration pages** — `docs/integration_<framework>.md` explains wiring a
  specific runtime. Use an existing page (e.g. [MCP](integration_mcp.md)) as a
  template.
- **Troubleshooting examples** — extend [`docs/troubleshooting.md`](troubleshooting.md)
  with a concrete failure you hit and how you fixed it.

New pages must be added to the `nav:` block in
[`mkdocs.yml`](https://github.com/dgenio/contextweaver/blob/main/mkdocs.yml).
Look for the `documentation` and `good first issue` labels.

## I want to add an adapter

Adapters convert between contextweaver's domain types and an external protocol
or framework. The full checklist is in
[CONTRIBUTING § Adding a new adapter](https://github.com/dgenio/contextweaver/blob/main/CONTRIBUTING.md#adding-a-new-adapter).
In short:

1. Implement `src/contextweaver/adapters/<name>.py` using the
   `from_<protocol>(..., into=...) -> list[ContextItem]` / `to_<protocol>(items) -> payload`
   convention.
2. Re-export the public surface from `adapters/__init__.py`.
3. Add `tests/test_adapters_<name>.py` covering both directions (round-trip
   where applicable).
4. Add a worked `examples/<name>_adapter_demo.py` and wire it into the
   `example:` target in the `Makefile`.
5. Add `docs/integration_<name>.md` and a row to the interop matrix in
   [How contextweaver Fits](interop.md).
6. **Never import the third-party SDK at module load** — use guarded imports
   and put heavy dependencies under `[project.optional-dependencies]`.

## I want to add a benchmark scenario

The committed scorecard at `benchmarks/scorecard.md` is regenerated
deterministically. Adding a scenario extends the gold dataset it reports on —
see [CONTRIBUTING § Adding a new benchmark scenario](https://github.com/dgenio/contextweaver/blob/main/CONTRIBUTING.md#adding-a-new-benchmark-scenario)
and [`docs/benchmarks.md`](benchmarks.md) "Known limits" for gaps worth
measuring.

1. Drop a JSONL file under `benchmarks/scenarios/` (one `ContextItem`-shaped
   event per line).
2. Target a **specific behaviour** the scorecard does not yet capture.
3. Run `make benchmark-matrix && make scorecard` to regenerate, and commit both
   `benchmarks/scorecard.md` and `benchmarks/results/latest.json`.

## I want to improve adoption / growth

Help adopters say "yes" faster.

- **Examples** — add a deterministic, network-free script under `examples/`
  (see [CONTRIBUTING § Adding a new example script](https://github.com/dgenio/contextweaver/blob/main/CONTRIBUTING.md#adding-a-new-example-script))
  and a row to the *Examples* table in `README.md`.
- **Demo assets** — the `contextweaver demo` scenarios power README and docs
  hero content.
- **Comparison pages** — sharpen [Where it fits](comparison.md) and the
  [Ecosystem Map](ecosystem.md) so readers can place contextweaver in their
  stack.
- **Honest limitations** — the [Launch Kit](launch_kit.md) sets the
  responsible-claims guardrails; keep new copy inside them.

## I want a good first issue

[`good first issue`](https://github.com/dgenio/contextweaver/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)
items are scoped to roughly one file or one module, with explicit acceptance
criteria and no deep architectural context required.

**Comment on the issue first so it can be assigned to you** — that avoids
duplicate work. A good first contribution in this repo:

- touches a single module and keeps it ≤ 300 lines;
- adds or updates a test for every changed public function;
- updates `CHANGELOG.md` under `## [Unreleased]`;
- passes `make ci` locally before the PR is opened.

## I am using an AI coding assistant

contextweaver is friendly to AI coding agents (Claude Code, GitHub Copilot
Agent Mode, Codex, …). Load these first, in order:

1. [`AGENTS.md`](https://github.com/dgenio/contextweaver/blob/main/AGENTS.md) —
   module map, conventions, and pipeline summary (the single source of truth).
2. [`.claude/CLAUDE.md`](https://github.com/dgenio/contextweaver/blob/main/.claude/CLAUDE.md)
   and [`.github/copilot-instructions.md`](https://github.com/dgenio/contextweaver/blob/main/.github/copilot-instructions.md)
   — assistant-specific operating overlays.
3. [`llms.txt`](https://github.com/dgenio/contextweaver/blob/main/llms.txt) /
   [`llms-full.txt`](https://github.com/dgenio/contextweaver/blob/main/llms-full.txt)
   — machine-readable repo summary.

Then follow the same rules everyone else does: no `print()` in library code,
no business logic in `__init__.py`, every public function gets a test, and
`make ci` is the gate. Tasks tagged `agent-friendly` are scoped for end-to-end
agent work.
