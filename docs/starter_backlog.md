# Curated starter backlog

These are ready-to-file starter issues for maintainers. Each is intentionally narrow, labeled, and acceptance-testable.

Use with the label scheme in `../LABEL_TAXONOMY.md`.

## 1. Add a cookbook recipe for firewall drilldown from an artifact handle

Labels: `area/docs`, `documentation`, `complexity/xs`, `good first issue`, `agent-friendly`

Goal: Add a short cookbook section showing how to retrieve raw tool output from an artifact handle after the context firewall summarizes it.

Likely files:

- `docs/cookbook.md`
- `examples/cookbook/firewall_drilldown_recipe.py`

Acceptance criteria:

- The docs explain when to show the summary versus when to drill down into the raw artifact.
- The snippet runs offline with `InMemoryArtifactStore`.
- The example avoids real secrets or customer data.

Suggested test command:

```bash
python examples/cookbook/firewall_drilldown_recipe.py
```

## 2. Add a docs note explaining guarded imports for optional adapters

Labels: `area/adapters`, `area/docs`, `documentation`, `complexity/xs`, `good first issue`, `agent-friendly`

Goal: Document the optional-dependency pattern used by adapters so base installs do not require every provider SDK.

Likely files:

- `docs/contributing_paths.md`
- `CONTRIBUTOR_ONRAMP.md`

Acceptance criteria:

- Shows a tiny `try/except ImportError` or local-import example.
- Explains that public adapter outputs should use contextweaver primitives, not provider SDK classes.
- Mentions a focused test command for one adapter test module.

Suggested test command:

```bash
python -m pytest tests/test_adapters_crewai.py
```

## 3. Add a benchmark fixture for a noisy support-tool catalog

Labels: `area/benchmarks`, `testing`, `performance`, `complexity/s`, `help wanted`, `agent-friendly`

Goal: Add a small routing fixture where many support tools share similar words, useful for comparing TF-IDF, BM25, and fuzzy scoring.

Likely files:

- `benchmarks/routing_gold.json`
- `examples/data/eval_routing.json`

Acceptance criteria:

- Adds at least five support queries and expected tool IDs.
- Fixture is deterministic and contains no real customer data.
- Existing benchmark commands still run.

Suggested test command:

```bash
python benchmarks/smoke_eval.py
```

## 4. Add a CLI help example for the demo scenario command

Labels: `area/docs`, `developer-experience`, `documentation`, `complexity/xs`, `good first issue`, `agent-friendly`

Goal: Improve docs for `contextweaver demo --scenario killer` by adding expected output hints and troubleshooting notes.

Likely files:

- `README.md`
- `docs/killer_demo.md`
- `docs/troubleshooting.md`

Acceptance criteria:

- The command is documented as no-network and no-API-key.
- Common import/path errors are addressed.
- The docs do not promise exact token counts unless generated from committed fixtures.

Suggested test command:

```bash
python -m contextweaver demo --scenario killer
```

## 5. Add an adapter demo smoke test for an existing adapter

Labels: `area/adapters`, `testing`, `complexity/s`, `help wanted`, `agent-friendly`

Goal: Add a focused smoke test for one existing adapter demo to prevent docs examples from drifting.

Likely files:

- `examples/*_adapter_demo.py`
- `tests/test_examples_*.py`

Acceptance criteria:

- The test imports and runs the demo entry point without network access.
- Optional provider SDKs are skipped cleanly when not installed.
- The test asserts a stable, minimal output property rather than the full text.

Suggested test command:

```bash
python -m pytest tests/test_examples_*.py
```

## 6. Add a small redaction example for tool output containing fake secrets

Labels: `area/context`, `security`, `documentation`, `complexity/s`, `help wanted`, `agent-friendly`

Goal: Show how secret redaction and firewalling interact for a fake tool output that contains test credentials.

Likely files:

- `docs/security_model.md`
- `examples/tool_wrapping.py`

Acceptance criteria:

- Uses clearly fake values only.
- Demonstrates that the prompt-visible summary does not expose the fake secret.
- Adds a short note about never using real secrets in tests or docs.

Suggested test command:

```bash
python examples/tool_wrapping.py
```

## 7. Add a gateway diagnostics troubleshooting table

Labels: `area/gateway`, `documentation`, `developer-experience`, `complexity/xs`, `good first issue`, `agent-friendly`

Goal: Add a table mapping common gateway diagnostics to likely causes and next steps.

Likely files:

- `docs/troubleshooting.md`
- `docs/gateway_spec.md`

Acceptance criteria:

- Includes at least five common diagnostic messages or classes.
- Each row has cause, impact, and next step.
- Does not require changes to gateway behavior.

Suggested test command:

```bash
python -m pytest tests/test_adapters_mcp_gateway.py
```

## 8. Add a minimal persistent-facts recipe

Labels: `area/context`, `documentation`, `complexity/xs`, `good first issue`, `agent-friendly`

Goal: Add a short recipe showing `ContextManager.add_fact_sync` and later answer-phase reuse.

Likely files:

- `docs/cookbook.md`
- `examples/cookbook/`

Acceptance criteria:

- Example runs offline.
- Demonstrates adding at least two facts and building an answer prompt.
- Notes that facts should be concise and non-secret.

Suggested test command:

```bash
python examples/cookbook/<new_recipe>.py
```

## 9. Add a routing ChoiceCard rendering example

Labels: `area/routing`, `documentation`, `complexity/xs`, `good first issue`, `agent-friendly`

Goal: Add a tiny example that routes a query, converts candidates to ChoiceCards, and renders the card text.

Likely files:

- `docs/tool_router.md`
- `examples/routing_demo.py`

Acceptance criteria:

- Uses a small in-memory catalog.
- Shows only the bounded shortlist, not full schemas.
- Output is deterministic.

Suggested test command:

```bash
python examples/routing_demo.py
```

## 10. Add an adapter checklist to a new provider integration doc

Labels: `area/adapters`, `documentation`, `integration`, `complexity/s`, `help wanted`, `agent-friendly`

Goal: Create a provider integration doc stub using the adapter checklist generated by `scaffolds/new_adapter.py`.

Likely files:

- `docs/integration_<provider>.md`
- `examples/<provider>_adapter_demo.py`

Acceptance criteria:

- The doc states install requirements, guarded-import behavior, and no-network demo limitations.
- The checklist includes provider-SDK-leak invariants.
- The page links to the generated example and test file.

Suggested test command:

```bash
python scaffolds/new_adapter.py example_provider --dry-run
```

## 11. Add a docs page for label usage and issue selection

Labels: `area/docs`, `developer-experience`, `documentation`, `complexity/xs`, `good first issue`, `agent-friendly`

Goal: Link the label taxonomy and starter backlog from contributor-facing docs.

Likely files:

- `CONTRIBUTING.md`
- `docs/contributing_paths.md`
- `LABEL_TAXONOMY.md`
- `docs/starter_backlog.md`

Acceptance criteria:

- Contributors can find labels, starter issues, and the adapter scaffold from one place.
- Links are relative and work in GitHub rendering.
- No behavior changes.

Suggested test command:

```bash
python -m py_compile scaffolds/new_adapter.py
```

## 12. Add an example showing async context builds in a non-voice loop

Labels: `area/context`, `documentation`, `developer-experience`, `complexity/s`, `help wanted`, `agent-friendly`

Goal: Add a small `asyncio.to_thread` example for context builds outside the Pipecat voice-agent guide.

Likely files:

- `docs/guide_agent_loop.md`
- `examples/minimal_loop.py`

Acceptance criteria:

- Shows sync ingest plus off-thread build.
- Explains that contextweaver does not make network calls during build.
- Runs without optional dependencies.

Suggested test command:

```bash
python examples/minimal_loop.py
```
