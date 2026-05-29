# LangGraph agent loop — captured run

Output of `python examples/architectures/langgraph_agent_loop/main.py`
from a clean checkout with LangGraph installed. Deterministic: routing is
seed-stable, tool responses are canned, and the firewall is hash-based on
artifact content. Without LangGraph installed the only difference is the
`agent loop engine:` line — the hand-rolled fallback produces identical
output otherwise.

```

============================================================================
contextweaver -- LangGraph agent-loop reference architecture
============================================================================
agent loop engine: langgraph
catalog: 36 tools across 9 namespaces

============================================================================
Turn t1
============================================================================
user: Our checkout API is throwing 500s — pull the recent error logs for the payments service
routed shortlist: ['infra.logs_search', 'infra.logs.tail', 'infra.deployments.list', 'admin.audit.export', 'admin.roles.create']
chosen: infra.logs_search  (intent='infra.logs_search', in shortlist)
route prompt:  naive all-tools 2,364 chars  ->  ChoiceCards 526 chars
firewall: 21,705 chars -> 49-char summary (artifact artifact:result:t1-tc)
answer prompt: 228 chars  (included=3, dependency_closures=0)

============================================================================
Turn t2
============================================================================
user: Summarize the likely root cause from those logs and draft an incident note
routed shortlist: ['incident.draft_note', 'incident.page_oncall', 'admin.audit.export', 'admin.roles.create', 'admin.roles.list']
chosen: incident.draft_note  (intent='incident.draft_note', in shortlist)
route prompt:  naive all-tools 2,364 chars  ->  ChoiceCards 561 chars
answer prompt: 538 chars  (included=6, dependency_closures=0)

============================================================================
What this showed
============================================================================
- LangGraph owned the route -> execute -> answer control flow.
- contextweaver bounded the catalog to a 5-card shortlist each turn.
- the large log result was firewalled to a summary on turn t1.
- turn t2's answer carried turn t1's firewalled result forward
  (cross-turn retention); the dependency_closure stage keeps every
  tool result paired with its originating tool call.
```
