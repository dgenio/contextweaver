# contextweaver

<!-- mcp-name: io.github.dgenio/contextweaver -->

[![CI](https://github.com/dgenio/contextweaver/actions/workflows/ci.yml/badge.svg)](https://github.com/dgenio/contextweaver/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/contextweaver.svg)](https://pypi.org/project/contextweaver/)
[![Python versions](https://img.shields.io/pypi/pyversions/contextweaver.svg)](https://pypi.org/project/contextweaver/)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/dgenio/contextweaver/badge)](https://scorecard.dev/viewer/?uri=github.com/dgenio/contextweaver)
[![Docs](https://img.shields.io/badge/docs-mkdocs--material-blue.svg)](https://dgenio.github.io/contextweaver)
[![GitHub Discussions](https://img.shields.io/github/discussions/dgenio/contextweaver)](https://github.com/dgenio/contextweaver/discussions)

> **Capture an agent's effective capability surface, commit it, and see semantically meaningful changes before deployment.**

ContextWeaver is currently testing a deliberately narrow product hypothesis:
**capability snapshot + semantic drift**.

Given an OpenAPI document, a captured MCP `tools/list` response, or a native
ContextWeaver catalog, the D1 experiment produces a deterministic normalized
snapshot that you can inspect, verify, and compare with a later candidate.
It does not require a model account, a gateway, a tool executor, or the Weaver
Stack.

**Status:** alpha, and specifically a **product experiment**. The implementation
works and is tested; the user-value hypothesis is not yet proven. The project is
actively measuring whether independent users keep this workflow after trying it
on real projects.

## Try the capability-drift experiment

Clone the repository and install that checkout so the maintained example
fixtures and the code under evaluation are guaranteed to match:

```bash
git clone --depth 1 https://github.com/dgenio/contextweaver.git
cd contextweaver
python -m pip install .
```

Run the maintained OpenAPI example:

```bash
python -m contextweaver.d1 snapshot examples/d1/openapi_before.json --source-type openapi --output ./cw-before.json
python -m contextweaver.d1 snapshot examples/d1/openapi_after.json --source-type openapi --output ./cw-after.json
python -m contextweaver.d1 inspect ./cw-after.json
python -m contextweaver.d1 verify ./cw-after.json
python -m contextweaver.d1 diff ./cw-before.json ./cw-after.json
```

The candidate fixture intentionally:

- makes `customer_id` required on the existing `listInvoices` capability;
- changes its description;
- adds a new `getInvoice` capability.

The diff separates capability additions/removals from changes to an existing
logical capability and reports the structured paths that changed. Contract
changes are separated from documentation-only changes. Changes involving fields
such as `required`, `type`, or `enum` are flagged as **potentially breaking** for
review.

That flag is intentionally conservative: ContextWeaver does **not** claim to be
a complete JSON-Schema compatibility checker.

Full walkthrough: [Capability drift experiment](docs/d1_capability_drift.md).

## Use it on your own source

### OpenAPI

```bash
python -m contextweaver.d1 snapshot ./openapi.yaml --source-type openapi --output ./capabilities.json
python -m contextweaver.d1 verify ./capabilities.json
```

After the API changes:

```bash
python -m contextweaver.d1 snapshot ./openapi.yaml --source-type openapi --output ./capabilities-candidate.json
python -m contextweaver.d1 diff ./capabilities.json ./capabilities-candidate.json
```

### Captured MCP tools

If you already have an MCP `tools/list` response saved as JSON:

```bash
python -m contextweaver.d1 snapshot ./tools-list.json \
  --source-type mcp \
  --output ./capabilities.json
```

For MCP, D1 compares tools by their upstream logical name so an input-schema
edit appears as a change to the same capability rather than an unexplained
remove/add pair. The historical schema-sensitive routing ID is retained
separately as `normalized_id` for inspection.

Capturing a live MCP server is a separate operation. `snapshot`, `inspect`,
`diff`, and `verify` do not execute discovered capabilities.

### Native ContextWeaver catalog

```bash
python -m contextweaver.d1 snapshot ./catalog.json \
  --source-type native \
  --output ./capabilities.json
```

## What `verify` means

`verify` checks the D1 snapshot contract: structure, deterministic ordering,
logical-ID uniqueness, and the canonical capability digest.

It is **not**:

- deployment approval;
- security certification;
- authentication or authorization;
- a guarantee that a tool implementation is correct;
- routing-quality evaluation;
- production runtime attestation.

## When not to use ContextWeaver D1

A negative answer is useful evidence for this project. Do **not** add
ContextWeaver just because capability snapshots sound tidy.

Use something simpler when:

- **ordinary Git diff, config review, and tests already make your capability
  changes obvious;**
- your tool/API surface is tiny and rarely changes;
- provider-native tool search is the only problem you are trying to solve;
- you need an agent loop, tool executor, IAM layer, or production orchestrator;
- maintaining another committed artifact costs more than the review/debugging
  problem it removes.

If you try D1 and conclude that Git/tests are cheaper, that is a valid product
result — please say so.

## What is being tested

The current survival experiment asks a stronger question than whether the code
works:

> Do capability snapshots and semantic drift reports improve a real
> review/manual/risk process enough that independent users keep them?

The project distinguishes:

```text
qualified exposure
  -> understood the problem
  -> chose to evaluate
  -> attempted setup
  -> reached first useful output
  -> used on a real project
  -> retained independently / removed
```

Stars, forks, downloads, a successful demo, and maintainer-created integrations
are not treated as retained adoption.

The controlling product decision is tracked in
[#758](https://github.com/dgenio/contextweaver/issues/758), and the distribution
quality gate is [#855](https://github.com/dgenio/contextweaver/issues/855).
Unassisted first success and retention are tracked in
[#658](https://github.com/dgenio/contextweaver/issues/658) and genuine adoption
in [#551](https://github.com/dgenio/contextweaver/issues/551).

## What about routing, context compilation, and the MCP gateway?

ContextWeaver already contains substantial historical runtime functionality.
That code still exists and currently shipped behavior should remain truthful and
safe, but **existing implementation is not evidence that the project should
keep expanding it**.

Two broader hypotheses are explicitly evidence-first:

- **D2 — bounded / phase-aware context compilation:** conditional. It must show
  consequential value beyond contemporary provider/runtime-native mechanisms.
- **D3 — custom deterministic tool selection:** a falsification track. It must
  beat modern provider-native tool search/deferred loading or a simple retrieval
  baseline on something target users actually care about.

During the D1 experiment, the project is not expanding routing sophistication,
runtime bundle machinery, memory/session surfaces, framework breadth, gateway
scope, vector stores, or model-assisted enrichment without a concrete external
blocker or approved falsification experiment.

If you are maintaining an existing integration that uses those historical
surfaces, the relevant documentation remains available:

- [Which historical pattern fits?](docs/which_pattern.md)
- [Context firewall](docs/context_firewall.md)
- [MCP Context Gateway architecture](docs/architectures/mcp_context_gateway.md)
- [MCP gateway security model](docs/security_model.md)
- [Comparison / alternatives](docs/comparison.md)
- [Ecosystem map](docs/ecosystem.md)

## Evidence and claims

The D1 implementation supports scoped engineering claims such as deterministic
snapshot construction under the documented source/adapter contract and
structured semantic-diff output. It does **not** yet support the stronger claim
that users need or retain the product.

The historical token-reduction headline is intentionally not used to sell D1.
The current evidence-integrity work for those older benchmark claims is tracked
in [#841](https://github.com/dgenio/contextweaver/issues/841).

See [Claims & evidence](docs/claims.md) for the claim registry and
[Capability drift experiment](docs/d1_capability_drift.md) for the exact D1
contract and limitations.

## Python API stability

D1 is intentionally exposed through:

```bash
python -m contextweaver.d1 ...
```

rather than being promoted immediately into the historical top-level CLI or a
large new public Python API. That is deliberate. The experiment should earn a
permanent surface through real retained use before the project takes on another
compatibility obligation.

## Part of the Weaver Stack — optionally

ContextWeaver can be used standalone. It has no hard dependency on the sibling
Weaver projects.

The wider Weaver Stack contains adjacent experiments/components for planning,
execution boundaries, guardrails, lessons, and evaluation. That ecosystem is
**not required** to evaluate D1, and Stack coherence is not a reason to preserve
a ContextWeaver feature that does not justify itself independently.

See the [Ecosystem map](docs/ecosystem.md) only if you actually need those
adjacent responsibilities.

## Install and compatibility

```bash
pip install contextweaver
```

Python 3.10–3.14 are covered by the repository CI matrix.

Current package version: **0.18.2**

| Project | Release |
|---|---|
| ContextWeaver (this repo, [v0.18.2](https://github.com/dgenio/contextweaver/releases/tag/v0.18.2)) | current package release |

The repository is pre-1.0. Prefer the latest supported patch release for bug and
security fixes, and check the changelog before relying on historical runtime
APIs.

## Current roadmap

The roadmap is intentionally a product-decision sequence, not a feature queue.

| Milestone | Status | Meaning |
|---|---|---|
| **v0.18.1 — D1 survival experiment baseline** | ✅ complete | Offline snapshot/inspect/diff/verify exists; user value remains unverified. |
| **v0.18.2** | ✅ current (v0.18.2) | D1 survival experiment and release-path recovery |
| **D1 distribution gate** | 🔬 evidence first | Make the front door understandable, recruit qualified evaluators, measure first success and retention. |
| **D1 decision** | ⏸ next decision | Continue, shrink further, or kill based on retained value after competent distribution. |
| **D2 / D3** | 🧪 conditional | Run only if D1 evidence or independent problem discovery justifies bounded falsification experiments. |

A green CI run does not advance this roadmap by itself.

## Contributing

The most valuable contributions during the survival experiment are narrow and
evidence-linked:

- a real D1 evaluator blocker;
- a semantic-diff case that is currently misleading or silently lost;
- deterministic normalization correctness;
- security/release maintenance for behavior the package still ships;
- negative evidence showing a simpler alternative wins.

Please do not add a framework adapter, routing policy, storage backend, runtime
phase, or ecosystem integration solely for completeness.

See [CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md) for repository
engineering conventions.

## Security

See [SECURITY.md](SECURITY.md) for supported-version and vulnerability-reporting
guidance. Do not include credentials, customer data, proprietary schemas, or
private prompts in public adoption/evaluation reports.

## Documentation

- [Documentation site](https://dgenio.github.io/contextweaver)
- [Capability drift experiment](docs/d1_capability_drift.md)
- [Claims & evidence](docs/claims.md)
- [Daily Driver guide](docs/daily_driver.md) — historical/runtime users
- [Cookbook](docs/cookbook.md) — broader shipped surfaces
- [FAQ](docs/faq.md)
- [Changelog](CHANGELOG.md)

## License

Apache-2.0. See [LICENSE](LICENSE).
