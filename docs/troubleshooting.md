# contextweaver Troubleshooting Guide

Quick reference for common integration problems, debugging techniques, performance
optimisation, and frequently asked questions.

---

## 1. Overview

Three tools cover the majority of debugging scenarios:

- **`BuildStats`** — inspect `pack.stats` after every `build_sync()` / `build()` call to
  see exactly what was kept, dropped, deduplicated, and why.
- **Store inspection** — query `event_log`, `artifact_store`, `fact_store`, and
  `episodic_store` directly to see what data the engine is working with.
- **Router debug trace** — pass `debug=True` to `Router.route()` to record the
  beam-search path taken for each query.

If a problem is framework-specific, check the integration guides in `docs/`:
[MCP](integration_mcp.md) · [A2A](integration_a2a.md).

---

## 2. Common Issues & Solutions

### Issue 1: Token Budget Too Tight

**Symptom:**
```
context build: phase=answer, included=1, dropped=19, tokens=350/6000
```
`pack.stats.included_count` is much lower than expected; most items are dropped.

**Cause:** Phase budget exhausted before all relevant items could be packed.

**Solution:**
```python
from contextweaver.config import ContextBudget
from contextweaver.context.manager import ContextManager
from contextweaver.types import Phase

# Defaults: route=2000, call=3000, interpret=4000, answer=6000
# Increase any phase that is too tight for your model / use-case
budget = ContextBudget(route=3000, call=5000, interpret=6000, answer=8000)
mgr = ContextManager(budget=budget)
```

Inspect `pack.stats.dropped_reasons` to confirm the cause:
```python
pack = mgr.build_sync(phase=Phase.answer, query="...")
print(pack.stats.dropped_reasons)  # e.g. {"budget": 15, "sensitivity": 2}
```

---

### Issue 2: Context Firewall Intercepted My Tool Result

**Symptom:**
```
firewall: intercepted item_id=tr1, summary_len=312
```
The LLM receives a truncated summary instead of the full tool output.

**Cause:** The firewall unconditionally intercepts **every** `tool_result` item.
Raw output is stored out-of-band in `ArtifactStore`; the LLM only sees a
compact summary.  This is by design — large outputs would otherwise consume the
entire token budget.

**Solution — access the raw artifact:**
```python
artifact_bytes = mgr.artifact_store.get("artifact:tr1")
full_result = artifact_bytes.decode("utf-8")
```

**Solution — plug in a custom summarizer:**
```python
from contextweaver.protocols import Summarizer

class MyDomainSummarizer(Summarizer):
    def summarize(self, text: str, metadata: dict) -> str:
        # Return a richer summary tailored to your domain
        return text[:1000]  # example: keep first 1000 chars

mgr = ContextManager(summarizer=MyDomainSummarizer())
```

**Why the firewall exists:** Raw tool results can be megabytes. Storing them
out-of-band and injecting summaries keeps prompts deterministic and
budget-bounded.

---

### Issue 3: Router Didn't Pick the Expected Tool

**Symptom:**
```
Query: "send an email"
Expected: send_email
Actual: send_sms, create_ticket
```

**Cause:** TF-IDF scoring favoured other tools; the expected tool's description
may not contain the keywords that appear in the query.

**Solution A — improve the tool description:**
```python
from contextweaver.types import SelectableItem

SelectableItem(
    id="send_email",
    name="send_email",
    description="Send an email message to a recipient address",  # "email" keyword
)
```

**Solution B — widen the beam and increase top-k:**
```python
router = Router(graph, items=catalog.all(), beam_width=5, top_k=10)
```

**Solution C — inspect the debug trace:**
```python
result = router.route("send an email", debug=True)
for step in result.debug_trace:
    print(step)
# Shows each beam expansion, node scores, and why items were (de-)prioritised
```

---

### Issue 4: `BuildStats` Shows All Items Dropped

**Symptom:**
```python
pack.stats.total_candidates  # e.g. 20
pack.stats.included_count    # 0
pack.stats.dropped_count     # 20
```

**Cause:** Two distinct failure modes — check `total_candidates` first:

- `total_candidates == 0` — items were excluded **before** the pipeline started.
  Phase-kind filtering happens in `generate_candidates()` (stage 1): items whose
  `ItemKind` is not in `policy.allowed_kinds_per_phase[phase]` are never added as
  candidates and never appear in `dropped_reasons`.
- `total_candidates > 0` and `dropped_count == total_candidates` — items entered the
  pipeline but were ejected. Check `dropped_reasons` for the cause.

Valid keys in `dropped_reasons`:

| Key | Meaning |
|-----|---------|
| `"budget"` | Item doesn't fit in the remaining token budget |
| `"kind_limit"` | `max_items_per_kind` cap reached for this `ItemKind` |
| `"sensitivity"` | Dropped by sensitivity policy |

**Diagnosis:**
```python
pack = mgr.build_sync(phase=Phase.answer, query="...")
print(pack.stats.total_candidates)  # 0 → items never generated; >0 → items dropped
print(pack.stats.dropped_reasons)
# {"budget": 18, "sensitivity": 2}  → budget is the main cause
# {"kind_limit": 20}               → max_items_per_kind cap reached

# If total_candidates == 0, check the phase-kind policy:
from contextweaver.config import ContextPolicy
from contextweaver.types import Phase, ItemKind

policy = ContextPolicy()
print(policy.allowed_kinds_per_phase[Phase.answer])
# Items whose kind is NOT in this list are silently excluded before scoring
```

**Solution — increase budget or adjust phase policy:**
```python
from contextweaver.config import ContextBudget, ContextPolicy
from contextweaver.types import ItemKind, Phase

budget = ContextBudget(answer=12000)

policy = ContextPolicy()
# Ensure tool_result items are permitted in the interpret phase
policy.allowed_kinds_per_phase[Phase.interpret].append(ItemKind.tool_result)

mgr = ContextManager(budget=budget, policy=policy)
```

---

### Issue 5: Deduplication Removed Important Context

**Symptom:**
```python
pack.stats.dedup_removed  # e.g. 8
```
Important items that should be distinct are treated as duplicates.

**Cause:** Default Jaccard similarity threshold is **0.85**. Items with ≥ 85 %
token overlap are collapsed.

> **Note:** Deduplication threshold configuration is tracked in
> [#182](https://github.com/dgenio/contextweaver/issues/182).
> Until that ships, the only workaround is to subclass `ContextManager`
> and override `_build()` to pass a custom `similarity_threshold` to
> `deduplicate_candidates()`.

Once [#182](https://github.com/dgenio/contextweaver/issues/182) ships, the fix will be:
```python
from contextweaver.config import ScoringConfig
from contextweaver.context.manager import ContextManager

# More conservative: only collapse near-exact duplicates
scoring = ScoringConfig(dedup_threshold=0.95)
mgr = ContextManager(scoring_config=scoring)

# Effectively disable deduplication
scoring = ScoringConfig(dedup_threshold=1.0)
mgr = ContextManager(scoring_config=scoring)
```

See [`docs/architecture.md`](architecture.md#deduplication) for algorithm details.

---

### Issue 6: `async build()` Hangs

**Symptom:**
```python
# Coroutine never completes
pack = await mgr.build(phase=Phase.answer, query="...")
```

**Cause:** A blocking call (e.g., slow summarizer, blocking I/O) inside a hook
or summarizer can stall the event loop.

**Solution:**
```python
# Option A: Use build_sync() when you're not in an async context
pack = mgr.build_sync(phase=Phase.answer, query="...")

# Option B: Offload blocking work to a thread pool inside your hook/summarizer
import asyncio

async def my_async_step():
    loop = asyncio.get_running_loop()
    pack = await loop.run_in_executor(None, mgr.build_sync, Phase.answer, "query")
```

---

### Issue 7: Events Not Appearing in Context (Candidates = 0)

**Symptom:**
```python
pack.stats.total_candidates  # 0
```

**Cause:** Items were never ingested, or their `ItemKind` is not allowed for
the current phase.

**Solution:**
```python
# Confirm items are in the event log
print(mgr.event_log.count())  # Should be > 0

# Confirm the phase allows the item kind you ingested
from contextweaver.types import Phase, ItemKind
from contextweaver.config import ContextPolicy

policy = ContextPolicy()
allowed = policy.allowed_kinds_per_phase[Phase.route]
print(allowed)  # e.g. [user_turn, plan_state, policy]
# If your item kind is not listed, add it or use a different phase
```

---

### Issue 8: Token Count Mismatch with External Framework

**Symptom:**
The total estimated tokens in `pack.stats` are much lower or higher than the
token count reported by LlamaIndex, LangChain, or another framework.

**Cause:** contextweaver uses a `CharDivFour` estimator by default (1 token ≈
4 characters). External frameworks often use `tiktoken` or a model-specific
tokeniser.

**Solution — compute totals from `pack.stats` and, if needed, use `TiktokenEstimator`:**
```python
from contextweaver.protocols import TiktokenEstimator

# Compute the total estimated tokens from a build
total_estimated_tokens = (
    sum(pack.stats.tokens_per_section.values())
    + pack.stats.header_footer_tokens
)

# Plug in the built-in tiktoken-backed estimator (requires `tiktoken` package)
mgr = ContextManager(token_estimator=TiktokenEstimator(model="gpt-4"))
```

---

### Issue 9: Graph Build Fails (`GraphBuildError`)

**Symptom:**
```
contextweaver.exceptions.GraphBuildError: cycle detected ...
```
or
```
contextweaver.exceptions.GraphBuildError: empty catalog
```

**Cause:**
- Empty catalog passed to `TreeBuilder`.
- A manually constructed `ChoiceGraph` introduced a cycle via `add_edge()`.

**Solution:**
```python
# Ensure catalog is non-empty before building
assert len(catalog.all()) > 0, "Catalog must not be empty"
graph = TreeBuilder(max_children=20).build(catalog.all())

# If you're building the graph manually, edges that form cycles raise
# GraphBuildError immediately — re-check your parent/child assignments.
```

---

### Issue 10: High Latency in Real-Time Agent

**Symptom:**
Context build takes 200–500 ms, causing perceptible lag.

**Cause:** Large event logs, aggressive deduplication (O(n²) comparisons), or
a slow custom summarizer.

**Solution — profile with `BuildStats`, then tune:**
```python
import time

start = time.perf_counter()
pack = mgr.build_sync(phase=Phase.answer, query="...")
elapsed = (time.perf_counter() - start) * 1000

print(f"Build time: {elapsed:.1f} ms")
print(f"Candidates processed: {pack.stats.total_candidates}")
print(f"Dedup removed: {pack.stats.dedup_removed}")
```

Optimisation checklist:
- Use tighter phase budgets to reduce how much content is included in the final
  pack; this does not reduce how many candidates are processed or scored.
- In async runtimes, offload `build_sync()` to a worker thread with
  `asyncio.to_thread()` or `loop.run_in_executor()` if you need to avoid blocking
  the event loop; `await mgr.build()` alone still runs the synchronous pipeline.
- Use the default `CharDivFour` estimator (faster than `tiktoken`).
- Keep the event log shallow: archive old turns to `episodic_store` and
  remove them from the active log.

---

## 3. Debugging Techniques

### Inspect `BuildStats`

```python
pack = mgr.build_sync(phase=Phase.answer, query="user query")

print(f"Total candidates:   {pack.stats.total_candidates}")
print(f"Included:           {pack.stats.included_count}")
print(f"Dropped:            {pack.stats.dropped_count}")
print(f"Dropped reasons:    {pack.stats.dropped_reasons}")
# e.g. {"budget": 12, "sensitivity": 3, "phase_filter": 0}

print(f"Dedup removed:      {pack.stats.dedup_removed}")
print(f"Dependency closures: {pack.stats.dependency_closures}")
print(f"Token usage:        {sum(pack.stats.tokens_per_section.values())}")
print(f"Tokens per section: {pack.stats.tokens_per_section}")
```

### Inspect the Event Log

```python
from contextweaver.types import ItemKind

# All events
for event in mgr.event_log.all():
    print(f"{event.id} ({event.kind.value}): {event.text[:60]}…")

# Filter by kind
tool_results = mgr.event_log.filter_by_kind(ItemKind.tool_result)
print(f"Tool results in log: {len(tool_results)}")
```

### Inspect Artifacts (Firewall Interceptions)

```python
# List all stored artifacts
for ref in mgr.artifact_store.list_refs():
    print(f"  {ref.handle}  label={ref.label}")

# Retrieve full raw content for a specific artifact
artifact_bytes = mgr.artifact_store.get("artifact:tr1")
print(artifact_bytes.decode("utf-8"))
```

### Inspect Routing Decisions

```python
# Enable debug trace (records each beam-search expansion)
result = router.route("send a reminder email", debug=True)

print("Candidates:", result.candidate_ids)
print("Scores:    ", result.scores)

for step in result.debug_trace:
    print(step)
```

### Inspect Facts and Episodes

```python
# Facts stored by the summarization / extraction pipeline
for key in mgr.fact_store.list_keys():
    for fact in mgr.fact_store.get_by_key(key):
        print(f"{key}: {fact}")

# Episodic summaries
for episode in mgr.episodic_store.all():
    print(f"{episode.episode_id}: {episode.summary[:80]}…")
```

### Enable Debug Logging

```python
import logging

logging.basicConfig(level=logging.DEBUG)
logging.getLogger("contextweaver.context").setLevel(logging.DEBUG)
logging.getLogger("contextweaver.routing").setLevel(logging.DEBUG)
```

This traces candidate counts, firewall interceptions, scoring, deduplication,
and beam-search expansions at every pipeline stage.

---

## 4. Performance Optimisation

### Latency Sources

| Stage | Cost | Notes |
|---|---|---|
| `generate_candidates` | O(n) | Scales with event log size |
| `dependency_closure` | O(n) | Usually fast |
| `apply_firewall` | O(n) + summarizer | Summarizer cost is caller-controlled |
| `score_candidates` | O(n) | TF-IDF index built once |
| `deduplicate_candidates` | O(n²) | Main hotspot for large candidate pools |
| `select_and_pack` | O(n log n) | Typically fast |
| `render_context` | O(n) | Fast string assembly |

### For Low-Latency Agents (real-time, voice)

```python
from contextweaver.config import ContextBudget
from contextweaver.context.manager import ContextManager

# Tighter budgets reduce how much context is retained in the final pack.
# To reduce candidates processed earlier in the pipeline, keep the event log
# short and rely on phase-kind / TTL / sensitivity filtering.
budget = ContextBudget(route=500, call=800, interpret=800, answer=1500)
mgr = ContextManager(budget=budget)

# In async runtimes, offload to a thread to avoid blocking the event loop:
# pack = await asyncio.to_thread(mgr.build_sync, Phase.answer, "...")

# Keep the event log short — archive old turns to episodic_store
```

### For Accuracy-Focused Agents (LlamaIndex, LangChain)

```python
# Larger budgets → more context preserved
budget = ContextBudget(route=3000, call=5000, interpret=6000, answer=10000)
mgr = ContextManager(budget=budget)

# Use the built-in tiktoken-backed estimator matching your LLM
from contextweaver.protocols import TiktokenEstimator

mgr = ContextManager(budget=budget, token_estimator=TiktokenEstimator(model="gpt-4"))
```

### For Large Tool Catalogs (100+ tools)

```python
# Route first, then build context only for the shortlisted tools
result = router.route(user_query, top_k=10)
shortlisted_ids = set(result.candidate_ids)

# Optionally filter ingested tool results to shortlisted tools only
relevant_events = [
    e for e in mgr.event_log.all()
    if e.parent_id in shortlisted_ids or e.id in shortlisted_ids
]
```

---

## 5. FAQ

**Q: What are the default token budgets?**

A: `ContextBudget(route=2000, call=3000, interpret=4000, answer=6000)`.
Tune them based on `pack.stats` and your model's context window.

---

**Q: Does the context firewall only fire for large tool results?**

A: No. The firewall intercepts **every** `tool_result` item, regardless of size.
Raw content is stored in `ArtifactStore`; the LLM always sees a compact summary.
Access raw data via `mgr.artifact_store.get("artifact:<item_id>")`.

---

**Q: How do I debug what was kept or dropped?**

A: Inspect `pack.stats` after every build:

```python
pack = mgr.build_sync(phase=Phase.answer, query="...")
print(pack.stats.included_count, pack.stats.dropped_count)
print(pack.stats.dropped_reasons)   # breakdown by cause
print(pack.stats.dedup_removed)     # near-duplicates removed
```

---

**Q: Does this work with [framework X]?**

A: contextweaver is framework-agnostic — it compiles context and you send the
prompt to any LLM or framework.  See the [integration guides](.) for
[MCP](integration_mcp.md) and [A2A](integration_a2a.md).
LlamaIndex, LangChain/LangGraph, OpenAI Agents SDK, and Google ADK guides are
in progress.

---

**Q: What's the default deduplication threshold?**

A: 0.85 Jaccard similarity. Items with ≥ 85 % token overlap are treated as
near-duplicates and collapsed. The higher-scoring item is retained.

---

**Q: Can I persist context across sessions?**

A: Yes. Use `fact_store` and `episodic_store` to persist memory across turns.
Serialise the event log to JSONL with `contextweaver ingest` / `replay` CLI
commands.

---

**Q: Does contextweaver work with open-source LLMs?**

A: Yes. contextweaver is LLM-agnostic. It compiles context; you send
`pack.prompt` to any model.

---

**Q: What's the typical latency overhead?**

A: 10–50 ms for a context build with a moderate event log (< 100 events).
The main hotspot is Jaccard-based deduplication (O(n²)). Use tighter budgets
and shorter event logs for real-time agents.

---

**Q: Can I disable the context firewall?**

A: The firewall applies to all `tool_result` items. To suppress its effect,
provide a pass-through summarizer that returns the full text, and ensure your
token budget is large enough to accommodate uncompressed results:

```python
from contextweaver.protocols import Summarizer

class PassThroughSummarizer(Summarizer):
    def summarize(self, text: str, metadata: dict) -> str:
        return text  # no truncation

mgr = ContextManager(summarizer=PassThroughSummarizer())
```

Note: raw content is still stored in `ArtifactStore` (this cannot be
disabled), but the LLM prompt will now contain the full text.

---

**Q: Can I use contextweaver without the routing engine?**

A: Yes. `ContextManager` is fully independent.  The routing engine (`Catalog`,
`TreeBuilder`, `Router`) is optional and only needed when you want to shortlist
tools from a large catalog.

---

**Q: Can I customize the scoring function?**

A: Custom scoring is not yet configurable (v0.1).  You can influence scoring
through `ScoringConfig` weights (`recency_weight`, `tag_match_weight`,
`kind_priority_weight`, `token_cost_penalty`):

```python
from contextweaver.config import ScoringConfig

scoring = ScoringConfig(recency_weight=0.5, tag_match_weight=0.1)
mgr = ContextManager(scoring_config=scoring)
```

---

**Q: How do I contribute?**

A: See [CONTRIBUTING.md](../CONTRIBUTING.md) for setup instructions, the
development workflow, and review guidelines.
