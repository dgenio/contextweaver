# 10-Minute Quickstart

This guide gets you to a working context build, a firewall-protected tool result,
and a routed tool shortlist in under 10 minutes.

> **In a hurry?** After `pip install contextweaver`, the built-in demo
> walks you through the same ideas in one command:
>
> ```bash
> contextweaver demo                                  # friendly walkthrough
> contextweaver demo --scenario large-catalog         # 1,000 tools → compact cards
> contextweaver demo --scenario huge-tool-output      # context firewall on a big tool result
> contextweaver demo --scenario mcp-gateway           # MCP gateway meta-tools end-to-end
> ```
>
> Each scenario is deterministic and network-free. Run `contextweaver demo
> --help` to see the full list.

Time budget:

- Prerequisites: 30 seconds
- Install: 30 seconds
- Your first context build: 3 minutes
- Try the context firewall: 4 minutes
- Try tool routing: 2 minutes
- What to try next: 1 minute

## Adopting from an existing chat history (5-line drop-in)

> **Already have an OpenAI, Anthropic, or Gemini agent?** You don't need to
> walk the full quickstart — drop contextweaver in front of your existing
> message history with one call. The full walkthrough below is for new agents.

If you have an OpenAI Chat Completions session saved as JSON, you can build
a context pack in five lines (plus imports):

```python
import json
from contextweaver.adapters.openai_messages import from_openai_messages
from contextweaver.context.manager import ContextManager
from contextweaver.types import Phase

mgr = ContextManager()
from_openai_messages(json.load(open("session.json")), into=mgr)
pack = mgr.build_sync(phase=Phase.answer, query="What did we decide?")
print(pack.prompt)
```

The adapter handles every OpenAI Chat Completions role — `system`, `user`,
`assistant` (with optional `tool_calls`), `tool` — and threads
`tool_call_id` ↔ `ContextItem.id` so `to_openai_messages(...)` is the exact
inverse for round-tripping back into the OpenAI SDK.

Anthropic and Google Gemini have sibling adapters with the same shape:

```python
from contextweaver.adapters.anthropic_messages import from_anthropic_messages
from contextweaver.adapters.gemini_contents import from_gemini_contents

# Anthropic Messages API: content blocks (text / tool_use / tool_result)
from_anthropic_messages(anthropic_messages, into=mgr)

# Google Gemini: contents[].parts[] (text / functionCall / functionResponse)
from_gemini_contents(gemini_contents, into=mgr)
```

All three adapters are pure stateless converters — they accept plain `dict`s
and never import a provider SDK at module load time. Session payloads may
contain sensitive prompt content; the adapters do not log message bodies
above `DEBUG` level.

> **Want to keep working with the provider SDK after building a pack?** Each
> adapter ships an inverse: `to_openai_messages`, `to_anthropic_messages`,
> `to_gemini_contents`. Round-trip equality holds for the representative
> fixtures in `tests/test_adapters_*.py`.

## 1. Prerequisites (30 seconds)

`contextweaver` requires Python 3.10 or newer.

Check your Python version:

```bash
python --version
```

Create and activate a virtual environment:

```bash
python -m venv .venv
```

Linux and macOS:

```bash
source .venv/bin/activate
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

If you see an error like `running scripts is disabled on this system`, either:

- run the activation script from **Command Prompt (cmd.exe)** instead:

  ```cmd
  .venv\Scripts\activate.bat
  ```

- or relax the execution policy for your current user in **PowerShell** (recommended only on machines you control):

  ```powershell
  Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
  ```

## 2. Install (30 seconds)

Install from PyPI:

```bash
pip install contextweaver
```

If you are working from a repository checkout instead, install the package in editable mode:

```bash
pip install -e ".[dev]"
```

Verify that the package imports from the Python environment you just activated:

```bash
python -c "import contextweaver; print(contextweaver.__version__)"
```

Expected output is the installed version, for example `0.14.0`. Then run the
network-free demo as an end-to-end smoke test:

```bash
contextweaver demo
```

You should see the friendly walkthrough start without `ModuleNotFoundError` or
`contextweaver: command not found`.

If your network blocks `openaipublic.blob.core.windows.net`, the demo or
token-budget helpers may print `tiktoken cl100k_base encoding unavailable`.
That warning is harmless: contextweaver falls back to the deterministic
chars/4 estimator and keeps enforcing budgets. To suppress it while keeping
exact `tiktoken` counts, pre-warm a `TIKTOKEN_CACHE_DIR` on a connected
machine and copy that cache into the offline environment; the
[troubleshooting guide](troubleshooting.md#offline-air-gapped-tiktoken-warning)
has the full workflow.

## 3. Your First Context Build (3 minutes)

Scenario: an agent receives a question, decides to query a database, and builds
an answer-phase prompt from the conversation history.

Save this as `first_agent.py`:

```python
"""Your first contextweaver context build."""

from contextweaver.context.manager import ContextManager
from contextweaver.types import ContextItem, ItemKind, Phase

mgr = ContextManager()
mgr.ingest(ContextItem(id="u1", kind=ItemKind.user_turn, text="How many active users do we have?"))
mgr.ingest(ContextItem(id="a1", kind=ItemKind.agent_msg, text="I'll check the database for you."))
mgr.ingest(
    ContextItem(
        id="tc1",
        kind=ItemKind.tool_call,
        text='db_query(sql="SELECT COUNT(*) FROM users WHERE active=true")',
        parent_id="u1",
    )
)
mgr.ingest(ContextItem(id="tr1", kind=ItemKind.tool_result, text="count: 1042", parent_id="tc1"))

pack = mgr.build_sync(phase=Phase.answer, query="active user count")

print("=== Compiled Context ===")
print(pack.prompt)
print("\n=== Build Stats ===")
print(f"Total candidates: {pack.stats.total_candidates}")
print(f"Included in prompt: {pack.stats.included_count}")
print(f"Dropped: {pack.stats.dropped_count}")
print(f"Deduplicated: {pack.stats.dedup_removed}")
```

Run it:

```bash
python first_agent.py
```

Expected output excerpt:

```text
=== Compiled Context ===
[TOOL RESULT [artifact:tr1]]
count: 1042

[TOOL CALL]
db_query(sql="SELECT COUNT(*) FROM users WHERE active=true")

[USER]
How many active users do we have?

[ASSISTANT]
I'll check the database for you.

=== Build Stats ===
Total candidates: 4
Included in prompt: 4
Dropped: 0
Deduplicated: 0
```

What just happened:

- You ingested four events into the event log.
- `build_sync()` ran the context pipeline for the `answer` phase.
- The prompt was compiled from the most relevant items and returned with build stats.

### Sensitivity & Default Drops

Every `ContextItem` has a `sensitivity` level. It defaults to `public`, and
the default policy is conservative: items at or above
`Sensitivity.confidential` are dropped before scoring and rendering.

```python
from contextweaver.config import ContextPolicy
from contextweaver.context.manager import ContextManager
from contextweaver.types import ContextItem, ItemKind, Phase, Sensitivity

mgr = ContextManager()
mgr.ingest(ContextItem(id="u1", kind=ItemKind.user_turn, text="public"))
mgr.ingest(
    ContextItem(
        id="c1",
        kind=ItemKind.user_turn,
        text="confidential",
        sensitivity=Sensitivity.confidential,
    )
)

pack = mgr.build_sync(phase=Phase.answer, query="any")
print(pack.stats.dropped_reasons.get("sensitivity", 0))  # 1
```

If you want to keep `confidential` items but still drop `restricted` items,
raise the floor:

```python
policy = ContextPolicy(sensitivity_floor=Sensitivity.restricted)
mgr = ContextManager(policy=policy)
```

If you want sensitive items to remain visible only as masks, use redact mode:

```python
policy = ContextPolicy(sensitivity_action="redact")
mgr = ContextManager(policy=policy)
```

## 4. Try the Context Firewall (4 minutes)

Problem: a large tool result can dominate the prompt if you include it verbatim.

Save this as `firewall_demo.py`:

```python
"""Show how the context firewall keeps prompts compact."""

from contextweaver.context.manager import ContextManager
from contextweaver.types import ContextItem, ItemKind, Phase

large_result = '{"users": [' + ', '.join(
    [
        f'{{"id": {i}, "name": "User{i}", "email": "user{i}@example.com"}}'
        for i in range(1, 101)
    ]
) + ']}'

mgr = ContextManager()
mgr.ingest(ContextItem(id="u1", kind=ItemKind.user_turn, text="List all users"))
mgr.ingest(ContextItem(id="tc1", kind=ItemKind.tool_call, text="list_users()", parent_id="u1"))
mgr.ingest(ContextItem(id="tr1", kind=ItemKind.tool_result, text=large_result, parent_id="tc1"))

pack = mgr.build_sync(phase=Phase.answer, query="user list")

print(f"Raw tool result size: {len(large_result)} chars")
print("\n=== Compiled Context ===")
print(pack.prompt)
print("\n=== Firewall Impact ===")
print(f"Prompt size after firewall: {len(pack.prompt)} chars")
print(f"Artifacts stored: {len(mgr.artifact_store.list_refs())}")
```

Run it:

```bash
python firewall_demo.py
```

Expected output excerpt:

```text
Raw tool result size: 6087 chars

=== Compiled Context ===
[USER]
List all users

[TOOL RESULT [artifact:tr1]]
{"users": [{"id": 1, "name": "User1", "email": "user1@example.com"}, ...

[TOOL CALL]
list_users()

=== Firewall Impact ===
Prompt size after firewall: ... chars
Artifacts stored: 1
```

What just happened:

- The tool result was processed by the firewall during context build (all `tool_result` items go through it by default).
- `contextweaver` stored the raw result in the artifact store.
- The prompt kept only a compact summary plus an artifact reference instead of the full payload.

## 5. Try Tool Routing (2 minutes)

Problem: when a catalog grows, the model should only see the most relevant tools.

Save this as `routing_demo.py`:

```python
"""Route a natural-language request to a focused tool shortlist."""

from contextweaver.routing.catalog import Catalog
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import SelectableItem

catalog = Catalog()
catalog.register(SelectableItem(id="t1", kind="tool", name="send_email", description="Send email to a recipient", tags=["notify", "team", "message"]))
catalog.register(SelectableItem(id="t2", kind="tool", name="db_query", description="Query the database", tags=["data"]))
catalog.register(SelectableItem(id="t3", kind="tool", name="create_ticket", description="Create support ticket", tags=["support"]))
catalog.register(SelectableItem(id="t4", kind="tool", name="send_sms", description="Send SMS message", tags=["notify", "team", "message"]))
catalog.register(SelectableItem(id="t5", kind="tool", name="schedule_meeting", description="Schedule a calendar meeting", tags=["calendar"]))

graph = TreeBuilder(max_children=3).build(catalog.all())
router = Router(graph, items=catalog.all(), beam_width=2, top_k=2)
result = router.route("notify the team about the deadline")

print("=== Query ===")
print("notify the team about the deadline")
print("\n=== Top Tools ===")
for item_id in result.candidate_ids:
    item = catalog.get(item_id)
    print(f"- {item.name}: {item.description}")
```

Run it:

```bash
python routing_demo.py
```

Expected output:

```text
=== Query ===
notify the team about the deadline

=== Top Tools ===
- send_sms: Send SMS message
- send_email: Send email to a recipient
```

What just happened:

- `TreeBuilder` organized the catalog into a bounded routing graph.
- `Router` scored the query against that graph and returned the top two candidates.
- Your model would now see a focused shortlist instead of the full catalog.

## 6. What to Try Next (1 minute)

Available now:

- [README](../README.md) for the top-level package overview
- [Concepts](concepts.md) for phases, the context firewall, and routing terms
- [Architecture](architecture.md) for the pipeline stages and module layout
- [MCP Integration](integration_mcp.md) for MCP adapters and session ingestion
- [A2A Integration](integration_a2a.md) for multi-agent adapter flows
- [Examples directory](../examples/) for larger end-to-end demos

Planned separately:

- Framework-specific integration guides are tracked in separate issues and are not part of this quickstart.

If you want a deeper local smoke test after this guide, run:

```bash
python -m contextweaver demo
```
