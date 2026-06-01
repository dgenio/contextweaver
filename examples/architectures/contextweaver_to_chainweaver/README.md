# contextweaver → ChainWeaver — reference architecture

> The Weaver-stack handoff: contextweaver **routes** a request to the right
> capability (advisory), ChainWeaver **executes** the deterministic flow
> behind it, and contextweaver **ingests** the result behind its context
> firewall. Builds on the ChainWeaver flow-import adapter (#334), the
> weaver-spec routing-contract mapping (#320), and the end-to-end seam (#353).

## Run it

```bash
python examples/architectures/contextweaver_to_chainweaver/main.py
```

(Or `make architectures` / `make example`.)

A captured run lives in [`OUTPUT.md`](OUTPUT.md).

## What this is (and isn't)

This is a **reference architecture**, not a tutorial recipe. It wires four
primitives together around the route → execute → ingest seam:

1. **Route (contextweaver).** A `Router` shortlists a catalog of ordinary
   tools **plus** ChainWeaver flows imported with
   `contextweaver.adapters.chainweaver.load_chainweaver_export`. Imported
   flows carry `kind="flow"` and route like any other candidate; a
   multi-step request ("summarize this customer's history") routes to the
   flow rather than to any single-step tool.
2. **Hand off (advisory).** The `RoutingDecision` is mapped to the neutral
   weaver-spec `RoutingDecision` via
   `contextweaver.adapters.weaver_contracts.to_weaver_routing_decision`. The
   decision is **advisory** — contextweaver selects a candidate; it does not
   execute or authorise it. The example also projects the selection into a
   host-side neutral candidate dict (the `ExecutionCandidate` shape from
   #320; see the caveat below).
3. **Execute (ChainWeaver).** A tiny in-process **stub** stands in for the
   ChainWeaver runtime — there is **no hard dependency** on ChainWeaver. It
   runs the selected flow deterministically and returns a large raw result.
4. **Ingest (contextweaver firewall).** The raw flow output goes back through
   `ContextManager.ingest_tool_result_sync`; the firewall stores the bytes
   out-of-band and the prompt only sees a compact summary, mapped to a
   weaver-spec `Frame`.

It is **mocked**: the ChainWeaver executor returns canned data, no real
runtime is invoked. The point is to show the seam, not to integrate with a
live ChainWeaver deployment.

## Notes

- **No ChainWeaver dependency.** The flow *export* is plain data; the
  executor is a stub. Standalone contextweaver use never imports ChainWeaver.
- **weaver-spec mapping is optional.** The `to_weaver_*` calls require the
  `contextweaver[weaver-spec]` extra. Without it the example prints a skip
  notice and continues with the native `RoutingDecision`.
- **`ExecutionCandidate` is host-side only.** weaver-spec does not (yet)
  define an `ExecutionCandidate` contract type, so this example projects the
  decision into a neutral dict rather than a library dataclass. See
  [`docs/weaver_spec_mapping.md`](../../../docs/weaver_spec_mapping.md).

## Related

- #334 — ChainWeaver flow → catalog import (`adapters/chainweaver.py`)
- #320 — routing outputs ↔ weaver-spec routing contracts (docs)
- #353 — this end-to-end example
