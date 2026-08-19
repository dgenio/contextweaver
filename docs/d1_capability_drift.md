# Capability drift experiment

> **Status: product experiment, not a stability promise.**
>
> This page tests one deliberately narrow ContextWeaver hypothesis: whether
> teams get enough value from deterministic capability snapshots and semantic
> drift reports to keep them in a real workflow. It does not require the
> routing engine, context firewall, gateway, a model account, or Weaver Stack.

## The problem being tested

An agent may depend on MCP tools, an OpenAPI surface, or a checked-in native
catalog. Those definitions change over time. A normal text diff can show that
bytes changed, but it often does not answer the review question directly:

> Which effective capabilities changed, and was the change documentation,
> invocation contract, or some other behavior/metadata?

ContextWeaver D1 tests whether answering that question is useful enough to
justify a product at all.

If ordinary Git diff and your existing tests already make capability changes
obvious, **do not add ContextWeaver for this**. That is a valid negative result
for the experiment.

## 60-second maintained example

From a repository checkout with ContextWeaver installed in the environment:

```bash
python -m contextweaver.d1 snapshot examples/d1/openapi_before.json \
  --source-type openapi \
  --output /tmp/cw-before.json

python -m contextweaver.d1 snapshot examples/d1/openapi_after.json \
  --source-type openapi \
  --output /tmp/cw-after.json

python -m contextweaver.d1 inspect /tmp/cw-after.json
python -m contextweaver.d1 verify /tmp/cw-after.json
python -m contextweaver.d1 diff /tmp/cw-before.json /tmp/cw-after.json
```

The candidate fixture intentionally changes two things:

1. `listInvoices` changes its request contract by making `customer_id`
   required, and its description changes;
2. a new `getInvoice` capability is added.

The diff reports the added capability and the exact JSON-pointer-like paths
changed on `listInvoices`. Contract paths are separated from documentation-only
changes. Changes to `required`, `type`, or `enum` are flagged
`potentially_breaking`; this is a review hint, **not** a claim that
ContextWeaver implements a complete JSON-Schema compatibility checker.

## Use a real OpenAPI document

No network call is made by the D1 path. External `$ref` fetching is not
performed by the existing OpenAPI adapter.

```bash
python -m contextweaver.d1 snapshot ./openapi.yaml \
  --source-type openapi \
  --output ./capabilities.json

python -m contextweaver.d1 verify ./capabilities.json
```

Commit `capabilities.json` if you want Git to preserve the effective normalized
surface rather than only the source document.

After the source changes:

```bash
python -m contextweaver.d1 snapshot ./openapi.yaml \
  --source-type openapi \
  --output /tmp/capabilities-candidate.json

python -m contextweaver.d1 diff \
  ./capabilities.json \
  /tmp/capabilities-candidate.json
```

Add `--check` only if you explicitly want any capability change to return exit
code 1 in CI:

```bash
python -m contextweaver.d1 diff \
  ./capabilities.json \
  /tmp/capabilities-candidate.json \
  --check
```

D1 does not currently implement a policy language that decides which changes
are allowed. The default command reports evidence and exits successfully;
`--check` is an opt-in coarse gate.

## Use an MCP `tools/list` snapshot

The D1 path accepts either a raw list of MCP tool definitions or an object with
a `tools` list, including the shape produced by
`scripts/capture_mcp_catalog.py`.

If you already have a captured response:

```bash
python -m contextweaver.d1 snapshot ./tools-list.json \
  --source-type mcp \
  --output ./capabilities.json
```

The snapshot uses the upstream MCP tool name as the **logical comparison
identity**. ContextWeaver's existing routing ID is retained separately as
`normalized_id`. This distinction matters because the routing identity can
change when an input schema changes; D1 must report that as one tool whose
contract drifted, not as an unexplained remove+add pair.

Capturing a live MCP server is a separate, explicit operation and may execute a
local server process. The snapshot/inspect/diff/verify operations themselves do
not execute discovered capabilities.

## Snapshot contract

`contextweaver.capability-snapshot@1` intentionally contains only enough data
for this experiment:

- source type and SHA-256 digest of the source bytes;
- normalized capabilities in deterministic logical-ID order;
- a SHA-256 digest of the canonical capability list;
- the normalized fields already produced by the MCP/OpenAPI/native adapters.

It deliberately omits local paths, timestamps and hostnames so identical source
bytes produce identical snapshot bytes on another machine.

`verify` checks structural validity, deterministic ordering, uniqueness, and the
capability digest. It is **not** production approval, authorization, security
certification, routing-quality evaluation, or deployment attestation.

## What this experiment does not test

D1 is not evidence for the historical ContextWeaver runtime architecture. In
particular, this path does not require or validate:

- custom tool routing or ChoiceCards;
- phase-aware runtime context compilation;
- the context firewall or artifact stores;
- MCP gateway/proxy operation;
- memory or session handoff;
- framework adapters;
- embeddings or vector stores;
- provider/model calls;
- Weaver Stack composition.

Those capabilities have to earn their own evidence. Do not add them to this
workflow to make the demo look more complete.

## What evidence matters

A successful command is not product validation. The experiment matters only if
qualified external users can reach a useful result and then choose to keep it.

When evaluating D1, record the funnel defined in #855:

```text
qualified exposure
  -> understood the problem
  -> chose to evaluate
  -> attempted setup
  -> reached first useful output
  -> used on a real project
  -> retained independently / removed
```

The most valuable feedback is concrete, including negative outcomes:

- Git diff/tests already solve this cheaply;
- the report is useful only as an occasional diagnostic;
- setup is more expensive than the value;
- a specific semantic change is missing or misleading;
- the snapshot becomes a retained CI/review artifact.

If users reach first value and then remove the workflow, that is stronger
product-failure evidence than today's low user count.

## Related decision records

- #758 — controlling product survival decision;
- #855 — distribution-quality gate;
- #856 — D1 implementation and exit criteria;
- #840 — neutral problem discovery;
- #658 / #551 — activation and retention evidence.
