# Contributing to contextweaver

Thank you for your interest in contributing!

This guide walks a new contributor end-to-end. If you are an existing
maintainer or an AI coding agent, [`AGENTS.md`](AGENTS.md) is the
authoritative operational reference; this file overlaps with it
deliberately so external contributors do not have to read both first.

## Getting started in two minutes

```bash
git clone https://github.com/dgenio/contextweaver
cd contextweaver
pip install -e ".[dev]"
pre-commit install
make ci     # the validation gate — everything below must pass
```

`pre-commit install` wires up `ruff format`, `ruff check --fix`, and
standard file-hygiene hooks to every `git commit`. Hooks may modify
files — re-stage with `git add` if needed.

If you only want to run the project without contributing, see the
[Quickstart](docs/quickstart.md) and [Showcase](docs/showcase.md) pages
instead.

## Where to start

- **First time?** Look for issues labelled
  [`good first issue`](https://github.com/dgenio/contextweaver/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22).
  These are scoped, well-defined, and don't require deep architectural
  context.
- **Want to discuss an idea before writing code?** Open a thread in
  [Discussions](https://github.com/dgenio/contextweaver/discussions)
  or file a `Feature request` issue.
- **Want to add an example, adapter, or benchmark scenario?** Each has
  a dedicated section near the bottom of this file.
- **Architectural context?** [`AGENTS.md`](AGENTS.md) is the module
  map and conventions reference; [`docs/architecture.md`](docs/architecture.md)
  is the deeper write-up.

## Development workflow

All commands run from the repo root after `pip install -e ".[dev]"`:

```bash
make fmt              # auto-format with ruff
make lint             # lint with ruff (also runs in pre-commit)
make type             # strict mypy type-check
make test             # pytest suite (1100+ tests, ~15 s)
make example          # run every example script end-to-end
make demo             # run `contextweaver demo`
make architectures    # run the reference architectures
make ci               # full validation gate (fmt + lint + type + test +
                      #                       schemas-check + example + demo)
make docs             # build the mkdocs site to ./site/
make docs-serve       # local docs server at http://127.0.0.1:8000
make benchmark        # write benchmarks/results/latest.json (deterministic)
make benchmark-matrix # full per-backend × per-size routing matrix
make scorecard        # regenerate benchmarks/scorecard.md from latest.json
make scorecard-check  # fail if committed scorecard.md is stale
```

`make ci` must pass before a PR can be merged. CI re-runs the same
gate on every PR.

If `make test` fails with `ModuleNotFoundError: No module named
'contextweaver'` on a fresh container, `pyproject.toml` pins
`pythonpath = ["src"]` for pytest and the Makefile uses `python -m
pytest` — both protect against editable-install resolution quirks.
Re-running after `pip install -e ".[dev]"` should resolve it.

## PR process

1. Fork the repository and create a feature branch from `main`.
2. Make your changes following the style guide below.
3. Add or update tests in `tests/`.
4. Run `make ci` and ensure it passes.
5. Update `CHANGELOG.md` under `## [Unreleased]`.
6. Open a pull request with a clear description of the change.

## Style guide

- **Python ≥ 3.10** — use `X | Y` union syntax, `match` statements where appropriate.
- **Core runtime dependencies** — the core ships with `tiktoken`, `PyYAML`, `rank-bm25`,
  plus `mcp` and `jsonschema` (the latter two are required by the MCP proxy / gateway
  runtimes — see `docs/gateway_spec.md` §4.4 and `adapters/mcp_*`).  Adding *another*
  entry to `dependencies` in `pyproject.toml` requires broad ecosystem use, a small
  wheel, and a default the library would otherwise approximate.  Heavy or
  runtime-specific packages go under `[project.optional-dependencies]` (e.g. `cli`,
  `otel`, `retrieval`, `ann`, `graph`) and must be loaded via guarded imports
  (`try: import x ... except ImportError: ...`).
- **Type hints everywhere** — all public functions and methods must be fully annotated.
- **Docstrings** — use Google-style docstrings on all public classes and functions.
- **Line length** — 100 characters maximum (enforced by ruff).
- **Imports** — `from __future__ import annotations` at the top of every file.
- **Module size** — target ≤ 300 lines per module (except `__main__.py`).
- **Determinism** — all algorithms must be deterministic; tie-break by ID / sorted keys.

## Testing requirements

- Every new public function must have at least one test.
- Tests live in `tests/test_<module_name>.py`.
- Use `pytest.mark.asyncio` for async tests (asyncio_mode = "auto" is set globally).
- Do not mock internal modules; use real in-memory implementations.

## Adding a new store backend

1. Implement the store class in `src/contextweaver/store/<name>.py`.
2. Export it from `src/contextweaver/store/__init__.py`.
3. Add tests in `tests/test_store_<name>.py`.
4. Update `StoreBundle` in `store/__init__.py` if appropriate.

## Adding a new example script

Examples live under `examples/` and run end-to-end as part of `make
example`. They are how external readers discover what contextweaver
can do.

1. Create `examples/<your_example>.py` with a module docstring that
   explains the scenario and a `main()` entrypoint.
2. Use **real public APIs** — no monkey-patched internals, no demo-
   special-case helpers. If you need a small fixture, inline it.
3. Make the example **deterministic** — fixed seeds, no network, no
   real LLM calls.
4. Add the file to the `example:` target in `Makefile` so it runs
   under CI.
5. Add a row to the *Examples* table in `README.md` with a one-line
   description.
6. Optional: link it from the [Cookbook](docs/cookbook.md) if it
   illustrates a specific recipe.

If the example is a full **reference architecture** (multi-file,
catalog YAML, captured `OUTPUT.md`), use the
[`examples/architectures/mcp_context_gateway/`](examples/architectures/mcp_context_gateway/)
or [`examples/architectures/slack_ops_bot/`](examples/architectures/slack_ops_bot/)
layout as the template, wire it into `make architectures`, and add a
docs page under `docs/architectures/`.

## Adding a new adapter

Adapters live under `src/contextweaver/adapters/` and convert between
contextweaver's domain types and external protocols (MCP, A2A, OpenAI
Chat Completions, Anthropic Messages, Gemini Contents, …).

1. Implement the adapter in `src/contextweaver/adapters/<name>.py`.
   Convention: `from_<protocol>(payload, into=ContextManager) ->
   ContextManager` for ingest, `to_<protocol>(pack) -> payload` for
   the inverse.
2. Re-export the public surface from
   `src/contextweaver/adapters/__init__.py`.
3. Add tests in `tests/test_adapters_<name>.py`. Cover both `from_*`
   and `to_*` (round-trip) where applicable.
4. Add a worked example under `examples/<name>_adapter_demo.py` (see
   `mcp_adapter_demo.py`, `a2a_adapter_demo.py` for the shape).
5. Add an integration guide under `docs/integration_<name>.md` if
   the adapter wraps a real third-party framework. The existing
   `docs/integration_mcp.md`, `integration_langchain.md`,
   `integration_llamaindex.md` are templates.
6. **Do not import the third-party SDK at module load.** Use guarded
   imports (`try: import x; except ImportError: ...`). Heavy or
   runtime-specific dependencies go under `[project.optional-
   dependencies]`.

## Adding a new benchmark scenario

The committed scorecard at `benchmarks/scorecard.md` is regenerated
deterministically from `benchmarks/results/latest.json`. Adding a
scenario means extending the gold dataset that the scorecard reports
on.

1. Drop a JSONL file under `benchmarks/scenarios/`. Each line is a
   single event in `ContextItem` shape — see the existing scenarios
   for the format.
2. The scenario should illustrate a **specific contextweaver behaviour**
   the scorecard does not yet capture (a no-op firewall on small
   payloads, a multi-tool turn, a sensitivity-floor case). See
   `docs/benchmarks.md` "Known limits" for currently-uncovered cases
   worth measuring.
3. Run `make benchmark-matrix && make scorecard` to regenerate.
4. Commit both `benchmarks/scorecard.md` and
   `benchmarks/results/latest.json` (accuracy numbers must be
   reproducible byte-for-byte across machines; latency numbers
   legitimately drift with hardware — call this out in the PR).
5. If the scenario lands as a **negative** or **zero-reduction**
   case (the firewall correctly no-op'd on small inputs, for
   example), that is *the point* and a good outcome — update
   `docs/benchmarks.md` "Known limits" to point at it as a concrete
   example.

See [`benchmarks/README.md`](benchmarks/README.md) for the harness
internals.

## Suggested tasks for AI coding agents

contextweaver is friendly to AI coding agents (Claude Code, GitHub
Copilot Agent Mode, Codex, etc.). The repo ships agent-facing guides
specifically:

- [`AGENTS.md`](AGENTS.md) — primary shared module map, conventions,
  and pipelines.
- [`.claude/CLAUDE.md`](.claude/CLAUDE.md) — Claude-specific
  operating overlay (Hard Rules, Validate Before Completing).
- [`.github/copilot-instructions.md`](.github/copilot-instructions.md)
  — Copilot Agent Mode guidance.
- [`llms.txt`](llms.txt) and [`llms-full.txt`](llms-full.txt) —
  machine-readable repo summary, regenerated by `make llms`.

If you are wiring contextweaver into an agent's tool-using session,
the most useful first reads (in order) are:

1. `docs/showcase.md` — runnable demos.
2. `docs/comparison.md` — where contextweaver fits in the stack.
3. `AGENTS.md` Module Map (around line 18).
4. `docs/architecture.md` — pipeline detail.

**Good first tasks for an agent on launch day:**

- Pick up a [`good first issue`](https://github.com/dgenio/contextweaver/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)
  — these are scoped to one file or one module each and have
  explicit acceptance criteria.
- Run `make ci` first to confirm the environment is healthy.
- Open a draft PR early so reviewers can guide the work.

Agents (and humans) must follow the same Hard Rules listed in
[`AGENTS.md`](AGENTS.md#hard-rules). The most load-bearing are: no
`print()` in library code (use logging or hooks), no business logic
in `__init__.py`, every public function gets a test, target ≤ 300
lines per module.

## Issue labels

Labels are GitHub-side and cannot be created from this repo — see
[`docs/agent-context/labels.md`](docs/agent-context/labels.md) for the
recommended set and how each label is used.

Common labels you will see:

- `good first issue` — scoped, well-defined, no deep architectural
  context required.
- `enhancement` — new feature or improvement.
- `bug` — reproducible defect.
- `documentation` — README, docs/, or example improvements.
- `help wanted` — maintainers would welcome an external contributor.
- `agent-friendly` — a task that AI coding agents can pick up
  end-to-end (clear acceptance criteria, small surface area).

If you want to claim a `good first issue`, comment on the issue first
so it can be assigned to you — that avoids duplicate work.
