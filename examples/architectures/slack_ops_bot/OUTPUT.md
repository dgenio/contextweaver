# Slack ops bot — captured run

Output of `python examples/architectures/slack_ops_bot/main.py` from a clean
checkout. Deterministic: routing is seed-stable, tool responses are canned,
the firewall is hash-based on artifact content. The investigation walks six
turns; the routing scoreboard at the end reports the intent / shortlist match
rate.

```
============================================================================
contextweaver -- Slack ops bot reference architecture
============================================================================
Loaded catalog: 48 tools from catalog.yaml

============================================================================
Turn 1
============================================================================
user:     look up the on-call engineer for api-gateway
routed:   ['identity.role_lookup', 'identity.user_lookup', 'oncall.lookup']
chosen:   oncall.lookup  (intent='oncall.lookup', in shortlist)
route prompt: 1 items / 11 tokens
answer prompt: included=3  dropped=0  dedup=0  closures=0  tokens=29

============================================================================
Turn 2
============================================================================
user:     tail the last hour of api-gateway logs
routed:   ['logs.tail', 'logs.archive', 'alerts.ack']
chosen:   logs.tail  (intent='logs.tail', in shortlist)
route prompt: 2 items / 20 tokens
firewall: 34,275 chars -> 501-char summary (artifact artifact:result:tc2)
answer prompt: included=6  dropped=0  dedup=0  closures=0  tokens=166

============================================================================
Turn 3
============================================================================
user:     show api-gateway deploy status
routed:   ['deploy.status', 'infra.status', 'deploy.unfreeze']
chosen:   deploy.status  (intent='deploy.status', in shortlist)
route prompt: 3 items / 27 tokens
answer prompt: included=9  dropped=0  dedup=0  closures=0  tokens=223

============================================================================
Turn 4
============================================================================
user:     roll back the api-gateway deploy to the previous build
routed:   ['deploy.rollback', 'deploy.start', 'alerts.ack']
chosen:   deploy.rollback  (intent='deploy.rollback', in shortlist)
route prompt: 4 items / 40 tokens
answer prompt: included=12  dropped=0  dedup=0  closures=0  tokens=257

============================================================================
Turn 5
============================================================================
user:     create a new incident ticket for this api-gateway outage
routed:   ['tickets.create', 'tickets.comment', 'oncall.escalate']
chosen:   tickets.create  (intent='tickets.create', in shortlist)
route prompt: 5 items / 54 tokens
answer prompt: included=15  dropped=0  dedup=0  closures=0  tokens=287

============================================================================
Turn 6
============================================================================
user:     show me the on-call schedule for tomorrow
routed:   ['oncall.schedule', 'feature.history', 'infra.status']
chosen:   oncall.schedule  (intent='oncall.schedule', in shortlist)
route prompt: 6 items / 64 tokens
answer prompt: included=18  dropped=0  dedup=0  closures=0  tokens=351

============================================================================
Persisted facts (carry across turns)
============================================================================
  deploy.api-gateway = rolled back from 9f12abc to 8a01def
  incident.api-gateway = OPS-4821
  oncall.api-gateway = alice@example.com

============================================================================
Routing scoreboard
============================================================================
intent in router top-3: 6/6  (100%)
Default scorer backend is TF-IDF. If your domain's tool names share vocabulary (e.g. lookup), try Router(scorer_backend='bm25' | 'fuzzy').
```

## What the captured numbers tell you

- **34 KB → 501-char firewall summary** on turn 2 (turn 2 logs dump). The
  raw bytes live in `mgr.artifact_store` under the printed handle; the
  prompt only sees the summary unless you drill in.
- **3 persisted facts** ride into every subsequent answer-phase build —
  the on-call engineer, the rollback record, and the ticket number all
  survive across turns without taking extra budget per turn.
- **Final prompt budget:** 351 answer-phase tokens out of the 4000-token
  answer budget (`ContextBudget(answer=4000)`). At this density the bot
  has plenty of headroom; tighten the budget to see `dropped_count` start
  showing up.
- **Routing scoreboard 6/6** — every turn's intent landed in the top-3
  shortlist, even though the 1000-item TF-IDF routing benchmark reports
  31% recall@5 (see the [scorecard](../../../benchmarks/scorecard.md)).
  A focused 48-tool catalog with namespaced tool names is the comfortable
  end of the recall curve.
