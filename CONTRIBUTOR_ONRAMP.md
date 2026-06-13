# Contributor on-ramp

Welcome! This repository is especially friendly to bounded contributions: adapters, docs recipes, benchmark fixtures, and small routing/context improvements.

## Pick a path

### Your first adapter

Adapters are the best community surface because they are valuable, bounded, and pattern-following.

1. Generate a local scaffold:

   ```bash
   python scaffolds/new_adapter.py my_provider --dry-run
   python scaffolds/new_adapter.py my_provider
   ```

2. Fill in the generated files:
   - `src/contextweaver/adapters/my_provider.py`
   - `tests/test_adapters_my_provider.py`
   - `docs/integration_my_provider.md`
   - `examples/my_provider_adapter_demo.py`

3. Keep provider SDK imports guarded. Import optional SDKs inside functions or behind `try/except ImportError` so a base `pip install contextweaver` remains lightweight.
4. Normalize provider-specific objects into contextweaver primitives. Do not leak provider SDK classes through public adapter return values.
5. Add a demo that runs without API keys or network access.
6. Run the focused tests, then the full suite if available.

Suggested labels for a first adapter issue: `area/adapters`, `integration`, `complexity/s`, `help wanted`, `agent-friendly`.

### Documentation and recipes

Good docs issues usually touch one page and include a runnable snippet or fixture. Prefer examples that work offline and avoid secret material.

Suggested labels: `area/docs`, `documentation`, `complexity/xs`, `good first issue`, `agent-friendly`.

### Benchmarks and fixtures

Benchmark contributions should add reproducible fixture data or a small scenario, not depend on external services, and document how to run locally.

Suggested labels: `area/benchmarks`, `performance` or `testing`, `complexity/s`, `help wanted`.

## What makes a good starter issue?

A starter issue should include:

- a one-sentence goal,
- likely files to edit,
- acceptance criteria,
- suggested test commands,
- non-goals and safety notes,
- labels from `LABEL_TAXONOMY.md`.

See `docs/starter_backlog.md` for ready-to-file starter issues.

## Local safety expectations

- Do not add calls to unknown external services.
- Do not commit secrets, tokens, credentials, or real customer data.
- Keep optional integration dependencies optional.
- Prefer deterministic examples that run with no API key.
- If a change affects the context firewall, routing, gateway, or adapter boundaries, include tests or an executable example.

## Contributors

Recognition can start simply: contributors are credited through Git history, release notes, and issue/PR references. If the project later adopts all-contributors, this file can become the contributor-facing landing page for that workflow.
