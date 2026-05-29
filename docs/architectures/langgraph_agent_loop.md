# LangGraph agent loop

> contextweaver running **inside** a LangGraph agent loop, not as a
> replacement for one. LangGraph owns control flow; contextweaver owns
> phase-aware context compilation (route → firewall → answer); tool
> execution stays outside contextweaver.

## TL;DR

| What | Where |
|---|---|
| The script | [`examples/architectures/langgraph_agent_loop/main.py`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/langgraph_agent_loop/main.py) |
| Captured output | [`examples/architectures/langgraph_agent_loop/OUTPUT.md`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/langgraph_agent_loop/OUTPUT.md) |
| Local README | [`examples/architectures/langgraph_agent_loop/README.md`](https://github.com/dgenio/contextweaver/blob/main/examples/architectures/langgraph_agent_loop/README.md) |

Run it:

```bash
python examples/architectures/langgraph_agent_loop/main.py
```

(Or `make architectures` / `make example`. Install the framework path with
`pip install 'contextweaver[langgraph]'`.)

## The boundary

| Concern | Owner |
|---|---|
| Control flow (`route -> execute -> answer`, the per-turn loop) | **LangGraph** `StateGraph` |
| Tool selection bounding (catalog → ChoiceCard shortlist) | **contextweaver** route phase |
| Large tool-result firewalling | **contextweaver** interpret phase |
| Budget-aware prompt + dependency-chain preservation | **contextweaver** answer phase |
| Executing a tool | The app / a tool runtime (here: mocked) |

## LangGraph is optional

The import is guarded. With LangGraph installed the real `StateGraph` drives
the loop; otherwise an equivalent hand-rolled loop calls the same node
functions in the same order. The output is identical apart from one
`agent loop engine:` banner line, so the example runs under a bare
`pip install contextweaver`. See the
[LangChain + LangGraph guide](../integration_langchain.md) for the broader
integration story.

## The scenario

A two-turn ops session; the "model" decision at each node is a deterministic
intent map standing in for an LLM holding the rendered ChoiceCards (no API
key, no network):

1. *"pull the recent error logs for the payments service"* → `infra.logs_search`
   returns a ~21 KB dump that the firewall compacts to a short summary.
2. *"summarize the root cause and draft an incident note"* → `incident.draft_note`;
   the answer build carries turn 1's firewalled result forward.

## Read next

- The [comparison page](../comparison.md) explains why contextweaver
  complements rather than replaces agent frameworks.
- The [catalog showcase](catalog_showcase.md) is the framework-free version
  of the same route → firewall → answer flow.
