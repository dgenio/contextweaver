# Curated Starter Backlog

This file lists starter-sized issues that are suitable for new contributors and coding agents. Each item is intentionally small, testable, and scoped to one file/module or one documentation page.

Recommended labels for all items below: `good first issue`, `help wanted`, `agent-friendly`.

## 1. Add a docs freshness note to lessons learned

Labels: `area/docs`, `complexity/good-first-issue`, `type/documentation`

Scope: one documentation page.

Problem: Some lessons-learned references can become stale as examples and benchmark numbers evolve.

Acceptance criteria:

- Add a short "Freshness" section explaining how to validate dated claims.
- Link to the current benchmark or scorecard page when mentioning measured numbers.
- Do not change benchmark results.

## 2. Add a cookbook recipe for firewall drilldown

Labels: `area/docs`, `area/context`, `complexity/good-first-issue`, `type/documentation`

Scope: one cookbook page or example.

Problem: New users need a compact recipe showing how to inspect raw artifact bytes after the context firewall summarizes a large result.

Acceptance criteria:

- Show a small local example using an in-memory artifact store.
- Include expected output shape.
- No network calls or credentials.

## 3. Add a benchmark fixture for a high-noise routing catalog

Labels: `area/benchmarks`, `area/routing`, `complexity/good-first-issue`, `type/testing`

Scope: one fixture file under benchmarks or examples data.

Problem: Routing examples should include a catalog with many similarly named distractor tools.

Acceptance criteria:

- Add a deterministic JSON fixture with at least 20 tools.
- Include 3 route queries and expected target ids.
- Document the fixture purpose in a top-level `_meta` or README note.

## 4. Improve CLI help text for demo scenarios

Labels: `area/gateway`, `complexity/good-first-issue`, `type/developer-experience`

Scope: CLI module and tests if present.

Problem: Demo scenario names are discoverable only by reading docs or source.

Acceptance criteria:

- CLI help lists available demo scenarios.
- Existing demo commands continue to work.
- Add or update a local test that checks help output.

## 5. Add a recipe for BYO tokenizer configuration

Labels: `area/context`, `area/docs`, `complexity/good-first-issue`, `type/documentation`

Scope: one docs page or cookbook recipe.

Problem: Users with custom model deployments need a short guide for token counting assumptions.

Acceptance criteria:

- Explain default token counting behavior.
- Show how to plug in or calibrate an alternative tokenizer if supported.
- Include a warning not to hard-code production budgets from demo values.

## 6. Add adapter scaffold coverage test

Labels: `area/adapters`, `complexity/good-first-issue`, `type/testing`

Scope: one test file.

Problem: The adapter scaffold should remain copy-pasteable as adapter conventions evolve.

Acceptance criteria:

- Add a test that reads scaffold template files and checks for required placeholders.
- Check that the template mentions guarded optional imports.
- No template rendering dependency required.

## 7. Add a LangChain memory cookbook note

Labels: `area/adapters`, `area/docs`, `complexity/good-first-issue`, `type/documentation`

Scope: one docs page.

Problem: Users need a minimal explanation of where external memory fits relative to contextweaver's context compiler.

Acceptance criteria:

- Explain that memory retrieval and context compilation are separate responsibilities.
- Include a small local pseudo-code snippet.
- Link to the existing memory integration material if present.

## 8. Add an example catalog validation failure

Labels: `area/routing`, `complexity/good-first-issue`, `type/testing`

Scope: one fixture and one test.

Problem: Contributors need a concrete example of invalid catalog input and the expected diagnostic.

Acceptance criteria:

- Add a minimal invalid catalog fixture.
- Add a test that asserts the relevant validation error or diagnostic text.
- Keep the fixture free of secrets and external URLs.

## 9. Add docs for adapter optional extras naming

Labels: `area/adapters`, `area/docs`, `complexity/good-first-issue`, `type/documentation`

Scope: adapter contribution docs.

Problem: Adapter PRs need consistent optional dependency names and error messages.

Acceptance criteria:

- Document preferred extra naming, for example `contextweaver[provider-name]`.
- Document import-error wording.
- Include one example guarded import block.

## 10. Add a smoke example for prompt budget overflow

Labels: `area/context`, `complexity/good-first-issue`, `type/testing`

Scope: one example or test.

Problem: Users benefit from seeing how dropped context is reported when the prompt budget is too tight.

Acceptance criteria:

- Construct local context items that exceed a small budget.
- Print or assert `dropped_count` and included section stats.
- No LLM or network dependency.

## 11. Add a recipe for routing scorer selection

Labels: `area/routing`, `area/docs`, `complexity/good-first-issue`, `type/documentation`

Scope: one docs page or cookbook recipe.

Problem: Existing examples mention TF-IDF, BM25, and fuzzy scoring, but contributors need a short decision guide.

Acceptance criteria:

- Explain when to try each scorer backend.
- Include a tiny local code snippet.
- State that evaluation should use deterministic fixtures.

## 12. Add a security note for adapter tests

Labels: `area/adapters`, `area/docs`, `area/security`, `complexity/good-first-issue`, `type/security`

Scope: adapter contribution docs or scaffold checklist.

Problem: Adapter tests should not leak provider SDK objects, credentials, or live API behavior into the core test suite.

Acceptance criteria:

- Add a short checklist for no credentials, no live network, and fake provider objects.
- Mention that provider-specific objects should be translated at the adapter boundary.
- Link to the adapter scaffold checklist.

## Maintainer triage note

GitHub label creation and applying labels to existing issues requires maintainer permissions. Use `LABEL_TAXONOMY.md` as the source of truth when creating missing labels and triaging this backlog.