# contextweaver → ChainWeaver — captured run

Output of `python examples/architectures/contextweaver_to_chainweaver/main.py`
from a clean checkout with the `[weaver-spec]` extra installed. Deterministic:
routing is seed-stable, the ChainWeaver runtime is a canned stub, and the
firewall is content-hashed. The run shows the route → execute → ingest seam.

```

============================================================================
contextweaver -> ChainWeaver reference architecture
============================================================================
Loaded catalog: 5 items (2 ChainWeaver flows + 3 tools)

============================================================================
1. Route (contextweaver)
============================================================================
query: summarize this customer's recent billing and order history
shortlist: ['chainweaver:customer_summary_flow', 'crm:lookup_customer', 'billing:get_invoice']
selected:  chainweaver:customer_summary_flow  (kind='flow')
routed to a ChainWeaver flow: True

============================================================================
2. Hand off (advisory routing decision)
============================================================================
host-side ExecutionCandidate (neutral; resolved to a ChainWeaver flow):
{
  "candidate_id": "chainweaver:customer_summary_flow",
  "candidate_type": "flow",
  "name": "Summarize customer history",
  "confidence": 0.581,
  "reason_codes": [
    "choicecard_match",
    "phase_route"
  ],
  "runtime": "chainweaver",
  "runtime_flow_id": "customer_summary_flow",
  "advisory": true
}
weaver-spec RoutingDecision id: rd-4437a513-6bc5-4670-9647-be58049b4a43

============================================================================
3. Execute (ChainWeaver stub)
============================================================================
ChainWeaver.execute(flow_id='customer_summary_flow', inputs={'customer_id': 'cust-42'})
raw flow result: 4,002 chars

============================================================================
4. Ingest result (contextweaver firewall)
============================================================================
firewall: 4,002 chars -> 501-char summary (artifact artifact:result:tc1)
weaver-spec Frame id: frame-cust-42 (capability chainweaver:customer_summary_flow)
answer prompt: included=3 dropped=0 tokens=145

============================================================================
Scoreboard
============================================================================
routed to flow:        True
firewall fired:        True
weaver-spec mapped:    decision=True frame=True
artifacts kept:        1
Seam: contextweaver ROUTES (advisory) -> ChainWeaver EXECUTES -> contextweaver INGESTS the result behind the firewall.
```
