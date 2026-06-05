# Two "Context Firewalls": Where the Seam Sits

The Weaver Stack uses the phrase *context firewall* in two places, and they
are **complementary, not competing**. This page is the single boundary
reference: who firewalls what, in what order, and where the seam between
agent-kernel and contextweaver lives.

## The two firewalls

| Firewall | Owner | What it does | Where |
|---|---|---|---|
| **Execution firewall** | agent-kernel | Raw driver/tool output is captured at the execution boundary and **never returned verbatim to the LLM**. The boundary emits a `Frame`. | At tool execution, *before* contextweaver sees anything. |
| **Context firewall** | contextweaver | Large or sensitive content is stored out-of-band in the artifact store; the prompt carries a compact summary + a typed `ArtifactRef`. | Context Engine pipeline, stage 4 ([`docs/architecture.md`](architecture.md)). |

They sit at **different layers**:

```text
   tool / driver output
          │
          ▼
 ┌──────────────────────┐   produces a Frame
 │  agent-kernel         │   (raw output never
 │  execution firewall   │    reaches the LLM)
 └──────────┬───────────┘
            │  Frame  (≈ contextweaver ResultEnvelope)
            ▼
 ┌──────────────────────┐   budgeted selection + packing;
 │  contextweaver        │   does NOT re-derive firewalling
 │  context firewall     │   from raw output on this path
 └──────────┬───────────┘
            │  ContextPack (summary + handle)
            ▼
          the LLM
```

The key rule: **agent-kernel firewalls execution; contextweaver firewalls the
context budget.** On the canonical path contextweaver consumes the `Frame`
agent-kernel already produced — it should not be the thing re-reading raw
driver output and re-summarising it.

## The canonical ingestion path (weaver-spec I-05)

weaver-spec invariant **I-05** ("contextweaver receives Frames, not raw
output") is satisfied through one entry point:

```python
from contextweaver.adapters.weaver_contracts import from_weaver_frame

# agent-kernel handed you a Frame at the execution boundary:
envelope = from_weaver_frame(frame)            # Frame → ResultEnvelope
mgr.ingest_envelope(tool_call_id, envelope)    # canonical seam — no re-firewall
```

`ContextManager.ingest_envelope()` appends a summary-only `ContextItem` that
carries the envelope's artifact handle. It performs **no** raw-output
firewalling — the `Frame` is already firewalled upstream. `ResultEnvelope` is
the contextweaver-native preimage of a `Frame`, so the core path needs no
`[weaver-spec]` extra; the adapter is only needed to translate the wire type.

## The non-canonical (standalone) path

When contextweaver runs *without* an agent-kernel-style execution boundary —
e.g. a plain MCP integration or a script that holds raw tool output — it owns
the firewall itself via:

- `ContextManager.ingest_tool_result(raw_output=...)`
- `ContextManager.ingest_mcp_result(...)`

These accept **raw** output and run the context firewall locally. They remain
fully supported for standalone use, but are **non-canonical for spec
compliance**: in a full Weaver Stack the execution boundary should firewall
first and hand over a `Frame`.

### Migrating to the canonical path

| If you currently call… | …and an execution boundary produces Frames, switch to |
|---|---|
| `ingest_tool_result(id, raw_output, ...)` | `ingest_envelope(id, from_weaver_frame(frame))` |
| `ingest_mcp_result(id, mcp_result, ...)` | firewall upstream, then `ingest_envelope(id, envelope)` |

No migration is required for standalone deployments that have no separate
execution boundary — the raw-output APIs are the right tool there.

## Cross-repo status

This page is contextweaver's side of the boundary. The matching statements in
[weaver-spec](https://github.com/dgenio/weaver-spec) (I-05) and agent-kernel
should mirror it so all three repositories agree. See
[`docs/weaver_spec_mapping.md`](weaver_spec_mapping.md) for the
`Frame ↔ ResultEnvelope` type mapping and the I-05 conformance row in the
project [README](https://github.com/dgenio/contextweaver#weaver-spec-compatibility).

## See also

- [Context Firewall](context_firewall.md) — the contextweaver-side firewall in detail.
- [weaver-spec mapping](weaver_spec_mapping.md) — `Frame ↔ ResultEnvelope`.
- [Architecture](architecture.md) — the eight-stage Context Engine pipeline.
