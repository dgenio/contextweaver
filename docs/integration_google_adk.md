# Google ADK / Vertex AI Integration

> Plug contextweaver's bounded-choice routing and context firewall into a
> [Google Vertex AI Agent Builder](https://cloud.google.com/vertex-ai/docs/agent-builder)
> tool-calling agent so Gemini sees a focused shortlist of tools and a
> budgeted prompt instead of the entire toolbelt and conversation history.

## Why

Vertex AI's agent builder abstracts away context management, which is
convenient but limits control. The three concrete pain points:

- **No phase-specific budgets.** Whatever you set in the agent config
  applies to every model call, regardless of whether the model is
  selecting a tool or producing the final answer.
- **Every tool is in the prompt.** Each `Tool` you register goes into
  the system instructions on every turn.
- **Multi-modal results are large.** OCR, embeddings, and search
  responses can easily exceed Gemini's per-message budget for a single
  function call.

contextweaver gives you per-phase budgets, a router for tool selection,
and an out-of-band firewall — all without taking over the model call.

## Prerequisites

```bash
pip install contextweaver google-cloud-aiplatform
gcloud auth application-default login
```

The examples below use the `google.cloud.aiplatform` SDK. The same
patterns work with the newer `google-genai` client; only the
`Tool` / `Agent` constructor names change.

## Architecture

```text
User query
   │
   ▼
contextweaver Router          ← all tools registered in Catalog
   │ (top-k shortlist)
   ▼
Vertex AI Agent (Gemini)      ← receives only the shortlist
   │ (tool call)
   ▼
contextweaver Firewall        ← intercepts large tool results
   │ (summary + artifact handle)
   ▼
contextweaver ContextManager  ← phase-specific budget compilation
   │ (pack.prompt)
   ▼
Gemini (final reply)
```

The hook points are identical to the
[OpenAI ADK integration](integration_openai_adk.md); only the SDK
surface differs.

## Minimal wiring

```python
from google.cloud import aiplatform

from contextweaver.context.manager import ContextManager
from contextweaver.routing.catalog import Catalog
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import ContextItem, ItemKind, Phase, SelectableItem


aiplatform.init(project="my-project", location="us-central1")


# 1. Define your tools (plain Python is enough — Vertex picks up the
# signature from the function spec).
def ocr_document(file_uri: str) -> str:
    """Extract text from a document image stored at *file_uri*."""
    return "... 5 KB of extracted text ..."


def summarize(text: str) -> str:
    """Return a 3-sentence summary of *text*."""
    return "..."


def extract_entities(text: str) -> str:
    """Return JSON with named entities found in *text*."""
    return '{"entities": [...]}'


FUNCTIONS = {
    "ocr_document": ocr_document,
    "summarize": summarize,
    "extract_entities": extract_entities,
}


# 2. Register every tool in contextweaver's Catalog as a SelectableItem.
catalog = Catalog()
for name, fn in FUNCTIONS.items():
    catalog.register(SelectableItem(
        id=name,
        kind="tool",
        name=name,
        description=(fn.__doc__ or "").strip().splitlines()[0],
        namespace="doc",
    ))
graph = TreeBuilder(max_children=8).build(catalog.all())
router = Router(graph, items=catalog.all(), top_k=2)
ctx_mgr = ContextManager()


# 3. Per-turn loop.
def respond(user_query: str, turn: int) -> str:
    ctx_mgr.ingest_sync(ContextItem(
        id=f"u{turn}", kind=ItemKind.user_turn, text=user_query,
    ))

    # Route to top-k tools so Vertex only sees the relevant ones.
    routed = router.route(user_query)
    selected = [FUNCTIONS[rid] for rid in routed.candidate_ids]

    # Phase.call → assemble arguments with a compact prompt.
    pack_call = ctx_mgr.build_sync(phase=Phase.call, query=user_query)
    agent = aiplatform.Agent(
        model_name="gemini-1.5-pro",
        tools=selected,
    )
    response = agent.run(pack_call.prompt)

    # Firewall every tool call result.
    for call in getattr(response, "tool_calls", []) or []:
        raw = FUNCTIONS[call.name](**call.arguments)
        ctx_mgr.ingest_sync(ContextItem(
            id=f"tc-{turn}-{call.name}", kind=ItemKind.tool_call,
            text=f"{call.name}(...)", parent_id=f"u{turn}",
        ))
        ctx_mgr.ingest_tool_result_sync(
            tool_call_id=f"tc-{turn}-{call.name}",
            raw_output=str(raw),
            tool_name=call.name,
        )

    # Phase.answer → final reply prompt; the firewall summary is in there.
    pack_answer = ctx_mgr.build_sync(phase=Phase.answer, query=user_query)
    final = agent.run(pack_answer.prompt)
    return str(final.text)
```

If you're using the newer [`google-genai`](https://github.com/googleapis/python-genai)
SDK, the only change is the `Agent` constructor:

```python
from google import genai

client = genai.Client(vertexai=True, project="my-project", location="us-central1")
response = client.models.generate_content(
    model="gemini-2.0-flash",
    contents=pack_call.prompt,
    config={"tools": selected},
)
```

## Routing-only integration (recommended for first adoption)

If you're not ready to manage your own prompt, just use the router and
keep Vertex's default context management:

```python
selected = [FUNCTIONS[rid] for rid in router.route(user_query).candidate_ids]
agent = aiplatform.Agent(model_name="gemini-1.5-pro", tools=selected)
response = agent.run(user_query)
```

This alone often cuts the per-turn token count substantially — a
catalogue of 50 tools narrowed to 3 saves thousands of tokens before
Gemini reads a word.

## Advanced patterns

- **Multi-modal payloads.** Vertex tool results frequently exceed 2 KB
  (the default `firewall_threshold`). Lower the threshold for
  text-heavy tools, or raise it for tools that already return compact
  JSON.
- **Episodic memory across sessions.** Persist
  `ctx_mgr.event_log.to_dict()` between turns so a user's prior context
  is replayable without going back to Vertex's session API.
- **Strict-mode reproducibility.** Set
  `ContextManager(profile=ProfileConfig.from_preset("balanced"))`
  to lock determinism mode and budgets in one object — useful when you
  log prompts for audit.
- **Cost-aware routing.** `SelectableItem.cost_hint` (mapped from MCP
  `costHint` or set directly) lets the router prefer cheaper tools when
  scores tie.

## Troubleshooting

- **Gemini still receives every tool.** Make sure you're filtering
  `tools=…` by `router.route(...)`. The SDK doesn't read contextweaver
  state implicitly.
- **Auth errors at `agent.run()`.** Run `gcloud auth
  application-default login` and confirm the project / location in
  `aiplatform.init(...)` match what Application Default Credentials
  resolves to.
- **Function-calling loops.** Use `exclude_ids` on subsequent
  `router.route()` calls so the agent stops re-recommending a tool it
  just used (issue
  [#112](https://github.com/dgenio/contextweaver/issues/112)).
- **Budget overruns.** Inspect `pack.stats.dropped_reasons` after each
  build — it pinpoints which pipeline stage rejected what.

## See also

- [How contextweaver Fits](interop.md) — boundary, hook points, non-goals
- [Cookbook](cookbook.md) — FastMCP, A2A, BYOT, firewall + drilldown
- [OpenAI ADK Integration](integration_openai_adk.md) — sister guide with
  the Swarm hand-off pattern
- [Vertex AI Agent Builder docs](https://cloud.google.com/vertex-ai/docs/agent-builder)
- Tracking issue: [#78](https://github.com/dgenio/contextweaver/issues/78)
