# Agent Runtime Loop Guide

This guide explains the reference runtime loop in
`examples/full_agent_loop.py`.

The loop demonstrates all four context phases in one deterministic flow:

1. `route` - shortlist candidate tools.
2. `call` - inject only the selected tool schema.
3. `interpret` - summarize tool output via the firewall.
4. `answer` - compose the final response context.

## Flow Diagram

```mermaid
flowchart TD
    U[User Query] --> R[Phase.route\nContextManager.build_route_prompt_sync]
    R --> C[ChoiceCards + routed candidate IDs]
    C --> M[Model selects tool_id]
    M --> H[Catalog.hydrate(tool_id)]
    H --> P[Phase.call\nContextManager.build_call_prompt_sync]
    P --> X[Simulated tool execution]
    X --> F[ingest_tool_result_sync\nFirewall stores raw artifact + summary]
    F --> I[Phase.interpret\nContextManager.build_sync]
    I --> A[Phase.answer\nContextManager.build_sync]
```

## Pseudo-code

```python
catalog = build_catalog_with_schemas()
router = Router(TreeBuilder().build(catalog.all()), items=catalog.all())
manager = ContextManager(budget=ContextBudget(route=500, call=800, interpret=600, answer=1000))

manager.ingest(user_turn)

route_pack, cards, route_result = manager.build_route_prompt_sync(goal, query, router)
tool_id = model_select(route_result.candidate_ids)

manager.ingest(tool_call)
call_pack = manager.build_call_prompt_sync(tool_id, query, catalog)

raw_result = simulate_large_json()
manager.ingest_tool_result_sync(tool_call_id, raw_result)
interpret_pack = manager.build_sync(phase=Phase.interpret, query=query)

answer_pack = manager.build_sync(phase=Phase.answer, query=final_query)
```

## Module Pointers

- `src/contextweaver/context/manager.py`
  - `build_route_prompt_sync()` for route-phase prompt + ChoiceCards.
  - `build_call_prompt_sync()` for selected-schema call prompts.
  - `ingest_tool_result_sync()` for firewall interception and envelope creation.
  - `build_sync()` for `interpret` and `answer` context compilation.
- `src/contextweaver/routing/router.py`
  - `Router.route()` to rank candidate tools.
- `src/contextweaver/routing/catalog.py`
  - `Catalog` and `Catalog.hydrate()` for schema hydration.
- `src/contextweaver/routing/cards.py`
  - Choice-card rendering used in route prompts.
- `src/contextweaver/config.py`
  - `ContextBudget` with per-phase token limits.

## When To Use Each Phase

| Phase | Primary goal | Typical contents | Budget posture |
| --- | --- | --- | --- |
| `route` | Choose tools | user intent, policy, compact cards | small |
| `call` | Generate arguments | selected tool schema, examples, constraints | medium |
| `interpret` | Understand result | tool call + summarized result + extracted facts | medium |
| `answer` | Compose final response | relevant history + interpreted findings + policy | largest |

## Running The Example

```bash
python examples/full_agent_loop.py
```

What you should see:

1. Four phase sections (`route`, `call`, `interpret`, `answer`).
2. Compiled prompt text for each phase.
3. BuildStats output for each phase, including token counts.
4. Firewall behavior in `interpret` (raw payload size > summarized text size).
