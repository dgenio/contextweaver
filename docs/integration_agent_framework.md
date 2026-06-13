# Microsoft Agent Framework integration

`contextweaver.adapters.agent_framework` bridges the
[Microsoft Agent Framework](https://github.com/microsoft/agent-framework) (the
successor to AutoGen and Semantic Kernel) to contextweaver, following the same
two-surface pattern as the CrewAI / Agno / Google ADK adapters.

## Two surfaces

### 1. Tool catalog (routing)

Convert `AIFunction` tools — or the equivalent plain-dict shape — into a
`SelectableItem` catalog so a Microsoft-stack agent routes through
contextweaver's bounded-choice router instead of putting every tool in the
prompt. Schemas are held for hydration, never embedded in cards.

```python
from contextweaver.adapters.agent_framework import (
    agent_framework_tools_to_catalog,
    load_agent_framework_catalog,
)
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder

# Plain-dict path — no SDK required:
catalog = agent_framework_tools_to_catalog(
    [{"name": "get_weather", "description": "Get the weather.", "parameters": {...}}]
)

# Live path — requires contextweaver[agent-framework]:
catalog = load_agent_framework_catalog(agent.tools)

items = catalog.all()
router = Router(TreeBuilder().build(items), items=items, beam_width=5)
result = router.route("what's the weather like?")
```

### 2. Thread ingestion (context)

Convert a thread's `ChatMessage` history into `ContextItem`s with `parent_id`
chains linking each function call to its result, so large tool results flow
through the firewall:

```python
from contextweaver.adapters.agent_framework import from_agent_framework_thread

items = from_agent_framework_thread(thread, into=manager)
```

| Message content | `ContextItem` |
|---|---|
| user text | `user_turn` |
| assistant / system text | `agent_msg` |
| `FunctionCallContent` | `tool_call` (JSON args as text) |
| `FunctionResultContent` | `tool_result` with `parent_id` → the call |

## Install

The plain-dict / message-dict paths work with no extra installed. For live
conversion of real Agent Framework objects:

```bash
pip install 'contextweaver[agent-framework]'
```

## Scope

Out of scope (per issue #430): .NET support, the framework's workflow /
orchestration features, and replacing its memory abstractions — only the tool
and thread-history surfaces are covered.

A runnable, network-free example lives at
[`examples/agent_framework_adapter_demo.py`](https://github.com/dgenio/contextweaver/blob/main/examples/agent_framework_adapter_demo.py).
