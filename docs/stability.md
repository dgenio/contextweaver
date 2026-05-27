# Stability and 1.0 Readiness

> contextweaver is still marked **Alpha** in package metadata. This page
> explains what that means, which surfaces are stable enough to adopt today,
> and what remains before Beta and 1.0.

The short version: the core context and routing engines are deterministic,
typed, tested, and used by the examples and benchmark harness. The project
remains Alpha because several adopter-facing boundaries are still being
clarified before a stronger public stability promise.

## Current Maturity

| Surface | Status | Compatibility expectation |
|---|---|---|
| Core dataclasses and enums | Public | Changes are deliberate and documented in the changelog. |
| Context Engine APIs | Public | `ContextManager`, `ContextPack`, `BuildStats`, `ContextPolicy`, and related documented types are intended for real use. |
| Routing Engine APIs | Public | `Catalog`, `TreeBuilder`, `Router`, `ChoiceCard`, `RouteResult`, and routing config types are intended for real use. |
| Store protocols | Public | Protocol-based store design is a core invariant. |
| Provider and protocol adapters | Public but still hardening | MCP, A2A, OpenAI, Anthropic, Gemini, FastMCP, CrewAI adapters are documented, but some edge cases remain pre-1.0. |
| MCP gateway/proxy runtime | Experimental | Useful and tested, but marked experimental while the CLI/runtime surface settles. |
| Optional extras | Mixed | Extras are available, but third-party API churn can affect adapter stability. |
| Reference architectures | Compatibility fixtures | They are runnable examples, not a production framework. |
| Internal modules | Internal | Modules beginning with `_` can change without notice. |

## Why Alpha

Alpha does not mean "untested." It means the project is not yet ready to make
a full Beta or 1.0 compatibility promise.

Current reasons to keep Alpha:

| Reason | Tracking |
|---|---|
| Provider-message adapter rendering has a known prompt-legibility bug. | [#308](https://github.com/dgenio/contextweaver/issues/308) |
| Community standards are not complete. | [#249](https://github.com/dgenio/contextweaver/issues/249) |
| Adopter-facing benchmark packaging is being added. | [#323](https://github.com/dgenio/contextweaver/issues/323) |
| Public API stability levels need to be visible in docs. | This page |
| Experimental runtime commands need clear labeling before a broader launch. | `contextweaver mcp serve`, gateway/proxy runtime |

## Beta Readiness Checklist

Beta means the project is ready for broader adopter trials, while still
allowing some pre-1.0 API refinement.

| Item | Status |
|---|---|
| Provider adapter drop-in path fixed and tested. | Open: [#308](https://github.com/dgenio/contextweaver/issues/308) |
| Community standards complete. | Open: [#249](https://github.com/dgenio/contextweaver/issues/249) |
| Adopter benchmark report published and linked. | In progress: [#323](https://github.com/dgenio/contextweaver/issues/323) |
| Public API surfaces documented with stability levels. | In progress: [#324](https://github.com/dgenio/contextweaver/issues/324) |
| Experimental CLI/runtime commands clearly marked. | Partially done |
| API reference published and linked from docs. | Done |
| At least one reference architecture maintained as a compatibility fixture. | Done |
| Benchmark limits and reproducibility documented. | Done, expanded by [#323](https://github.com/dgenio/contextweaver/issues/323) |
| Changelog and migration notes maintained under `## [Unreleased]`. | Done |

Recommended Beta classifier change: move from
`Development Status :: 3 - Alpha` to `Development Status :: 4 - Beta` only
after the open blocker rows above are closed or intentionally deferred.

## 1.0 Readiness Checklist

1.0 means public APIs are frozen enough for normal semantic-versioning
expectations.

| Item | Required before 1.0 |
|---|---|
| Public import map reviewed | Decide which documented `contextweaver.*` imports are stable long-term. |
| Experimental surfaces isolated | Keep experimental gateway/runtime features labeled or move them behind clear namespaces. |
| Deprecation policy enforced | Deprecated public APIs warn before removal. |
| Migration guide ready | Document any pre-1.0 breaking changes and replacements. |
| Benchmark report stable | Make sure benchmark methodology, limits, and regeneration flow are current. |
| Security-sensitive defaults reviewed | Re-check sensitivity and firewall defaults before declaring 1.0. |
| Protocol compatibility reviewed | Re-check MCP, A2A, and weaver-spec adapter behavior against current upstream specs. |
| Docs and examples pass cleanly | `make ci` and `make docs` should be clean on a release branch. |

## Deprecation Policy

Before 1.0:

- Public APIs may still change when correctness or clarity requires it.
- The project should prefer warnings, migration notes, and changelog entries
  over surprise removals.
- Experimental surfaces may change faster, but docs should say so.

After 1.0:

- Breaking changes to stable public APIs should wait for a major release.
- Deprecated public APIs should be documented for at least one minor release
  before removal.
- Internal modules remain internal even after 1.0.

## Import Guidance

| Prefer | Avoid |
|---|---|
| Documented classes and functions linked from the API reference. | Modules or helpers whose names start with `_`. |
| `contextweaver.context.manager.ContextManager` for builds. | Reaching into individual context pipeline internals unless extending the library. |
| `contextweaver.routing.*` public classes for catalogs and routing. | Depending on private scorer or graph helper functions. |
| Store protocols and concrete store classes documented in `store/`. | Mutating in-memory store internals directly. |

If a public-looking path is not documented and you need it, open an issue. That
is exactly the kind of API-boundary feedback that should be resolved before 1.0.

## Release Messaging

Use this public phrasing until the Beta checklist is complete:

```text
contextweaver is Alpha: the core context and routing engines are tested and
usable today, while gateway/runtime and adapter edges are still being hardened
before Beta and 1.0.
```

Avoid saying:

- "production-ready for every agent stack"
- "1.0-stable API"
- "drop-in replacement for memory, RAG, or agent frameworks"
- "guaranteed cost reduction"

See the [Launch Kit](launch_kit.md) for reusable wording and claim guardrails.
