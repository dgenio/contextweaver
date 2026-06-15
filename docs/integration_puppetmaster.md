# Puppetmaster Integration

> Consume [Puppetmaster](https://github.com/dgenio/puppetmaster)-style job artifacts,
> worker summaries, logs, and follow-up reads through contextweaver so the LLM
> never sees raw multi-KB artifacts unless it explicitly asks for them.

## Why

Puppetmaster orchestrates long-running, multi-step jobs. Each job produces:

- **Worker summaries** (small, structured)
- **Execution logs** (often multi-KB)
- **Intermediate artifacts** (files, JSON blobs, traces)
- **Follow-up prompts** (conditional next steps)

Dumping all of this into the model context on every turn causes the same three
failures contextweaver exists to prevent:

1. **Token bloat.** A single worker log can consume 30 % of a 32 K context window.
2. **Quality drop.** Needle-in-haystack accuracy degrades as the prompt grows.
3. **Sensitivity leakage.** Logs may contain credentials, internal URLs, or PII.

contextweaver treats Puppetmaster outputs as firewalled tool results: raw bytes
go to the artifact store; the LLM sees compact summaries, typed handles, and
extracted structured fields. When the model needs the full log or artifact, it
references the handle and `tool_view` returns the precise slice.

## Scope

- **In scope:** ingesting job artifacts and worker summaries, drilldown via
  handles/selectors, per-phase budgeting over job history, bounded follow-up
  context compilation.
- **Out of scope:** job supervision, worker orchestration, or replacing
  Puppetmaster's scheduler. contextweaver is a context consumer, not a job
  controller.

## Architecture

```text
┌─────────────────────────────────────────────────────────────────┐
│ Puppetmaster runtime                                            │
│   • job scheduler + worker pool                                 │
│   • produces: summaries, logs, artifacts, follow-ups            │
└───────────────────────────────┬─────────────────────────────────┘
                                │  (your adapter)
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│ contextweaver                                                   │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │ ContextManager                                          │   │
│   │   ├─ ingest_tool_result_sync(summary, raw_log)        │   │
│   │   │      → firewall stores raw log in artifact_store  │   │
│   │   │      → ContextItem carries summary + ArtifactRef  │   │
│   │   ├─ build(phase=Phase.route)                         │   │
│   │   │      → choose which job/history is relevant       │   │
│   │   └─ build(phase=Phase.answer)                        │   │
│   │          → budgeted prompt over job history           │   │
│   └─────────────────────────────────────────────────────────┘   │
│                         │                                       │
│                         ▼                                       │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │ ArtifactStore                                          │   │
│   │   • raw logs / artifacts by handle                      │   │
│   │   • retrieved only via drilldown (tool_view)            │   │
│   └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

## Ingesting a job result

A Puppetmaster job completion is just another tool result. Ingest it via the
firewall so large payloads are stored out-of-band automatically:

```python
from contextweaver import ContextManager
from contextweaver.config import ContextBudget, ContextPolicy

mgr = ContextManager(
    budget=ContextBudget(route=500, call=800, interpret=600, answer=1200),
    policy=ContextPolicy(),
)

job_summary = "Worker 'train-model' completed. Accuracy: 0.94. Epochs: 50."
raw_log = """2024-06-01T12:00:01Z [INFO] Starting epoch 1...
2024-06-01T12:05:23Z [INFO] epoch 1 loss=0.42
...
2024-06-01T14:32:11Z [INFO] epoch 50 loss=0.03 accuracy=0.94
2024-06-01T14:32:12Z [INFO] Saving checkpoint to /runs/epoch-50.pt
"""

mgr.ingest_tool_result_sync(
    tool_call_id="job:train-model:42",
    raw_output={"summary": job_summary, "log": raw_log},
)

# The firewall stores the raw log in the artifact store.
# The event log contains a compact ContextItem with a handle.
```

The firewall intercepts the log if it exceeds the configured threshold (default
2 048 bytes). The LLM sees the summary; the full log lives in the artifact store
under a typed `ArtifactRef` handle.

## Budgeting across job history

Treat job history like any other turn sequence. Phase budgets keep the prompt
bounded even when dozens of prior jobs exist:

```python
from contextweaver.types import Phase

# Route phase: select relevant tools/jobs for the current goal.
route_pack = mgr.build_sync(phase=Phase.route, query="check latest training run")

# Answer phase: compose a response over the accumulated history.
answer_pack = mgr.build_sync(phase=Phase.answer, query="summarise training progress")
```

| Phase | What it covers | Budget posture |
|---|---|---|
| `route` | Pick which job, worker, or follow-up tool is relevant | Small |
| `call` | Generate arguments for a selected tool | Medium |
| `interpret` | Digest a fresh job result with prior context | Medium |
| `answer` | Compose a final summary across multiple jobs | Largest |

Use tight `route` budgets so the model can't "read" the entire job history just
to pick a tool. Let `answer` carry the larger budget because that is where the
model synthesises findings.

## Drilldown and follow-up reads

When the summary is not enough, the model can request the full artifact via the
handle. In a gateway setup this is `tool_view`; in a standalone script you read
directly from the store:

```python
# Gateway path (MCP proxy / gateway)
from contextweaver.adapters.mcp_gateway import tool_view

# The model sends back the artifact handle from the summary.
full_log = tool_view(mgr, handle="artifact:job:train-model:42")
```

```python
# Standalone path
handle = "artifact:job:train-model:42"
assert mgr.artifact_store.exists(handle)
full_bytes = mgr.artifact_store.get(handle)
```

Both paths are gated by the artifact store; the LLM never receives raw bytes
unless a deliberate drilldown happens.

## Handling follow-up prompts

Puppetmaster can emit structured follow-up prompts ("retrain with lr=0.001",
"evaluate on validation set B"). Treat these as `ContextItem` candidates with
`ItemKind.tool_call` so the router can include them in the shortlist:

```python
from contextweaver.types import ContextItem, ItemKind

follow_up = ContextItem(
    id="suggestion:epoch-50",
    kind=ItemKind.tool_call,
    text="Suggested follow-up: evaluate on validation set B",
    metadata={
        "source": "puppetmaster",
        "job_id": "train-model:42",
        "suggested_action": "evaluate",
        "args": {"dataset": "validation-B"},
    },
)
mgr.ingest(follow_up)
```

During `Phase.route`, the follow-up competes for budget with other candidates.
If scored highly, it surfaces in the prompt as a routed suggestion the model can
choose to act on or ignore.

## Sensitivity

Job logs often contain internal hostnames, tokens, or file paths. The firewall
does not erase them — it stores the raw log unchanged in the artifact store.
Apply a `SensitivityClassifier` or `RedactionHook` if the *summary* itself must
also be scrubbed before reaching the LLM:

```python
from contextweaver.context.sensitivity import MaskRedactionHook
from contextweaver.config import ContextPolicy

policy = ContextPolicy(
    sensitivity_floor=Sensitivity.internal,
    redaction_hooks=["mask"],
)
mgr = ContextManager(budget=budget, policy=policy)
```

See [`context_firewall.md`](context_firewall.md) and
[`security_model.md`](security_model.md) for the full firewall and sensitivity
guides.

## Summary

| Puppetmaster output | contextweaver treatment |
|---|---|
| Worker summary | Ingest as `tool_result` summary (LLM-visible) |
| Execution log | Firewall → artifact store; handle in summary |
| Intermediate artifact | Firewall → artifact store; drilldown on demand |
| Follow-up prompt | Ingest as `tool_call` candidate for routing |
| Multi-job history | Per-phase budget (`route` / `answer`) keeps prompt bounded |

Keep Puppetmaster as the orchestrator of record. contextweaver only decides
what the LLM sees this turn and how much of it fits the budget.
