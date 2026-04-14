"""LangChain memory replacement demo.

Shows how contextweaver replaces LangChain's ``InMemoryChatMessageHistory``
(equivalent to ``ConversationBufferMemory``) with phase-specific budgeted
context compilation and a context firewall.

The **without** path accumulates the full conversation into a flat history —
identical to the naive LangChain memory pattern, with no budget enforcement.
The **with** path compiles each LLM call through a dedicated phase budget so
the prompt never bloats beyond what that phase actually needs.

No API key required.  A deterministic :func:`mock_llm` is used throughout.

Run standalone::

    python examples/langchain_memory_demo.py

Or via the project test suite::

    make example
"""

from __future__ import annotations

try:
    from langchain_core.chat_history import InMemoryChatMessageHistory
    from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

    _LANGCHAIN_AVAILABLE = True
except ImportError:  # pragma: no cover
    _LANGCHAIN_AVAILABLE = False

from contextweaver.config import ContextBudget
from contextweaver.context.manager import ContextManager
from contextweaver.protocols import CharDivFourEstimator
from contextweaver.types import ContextItem, ItemKind, Phase

# Large database result — triggers the context firewall (> 200 chars).
# Six users + query stats keeps this realistic and exercises the 500-char
# summary truncation in _default_summary.
LARGE_DB_RESULT = (
    '{"users": ['
    '{"id": 1, "name": "Alice Smith", "email": "alice@example.com",'
    ' "department": "Engineering", "active": true, "role": "admin",'
    ' "last_login": "2026-04-10T09:00:00Z", "projects": ["infra", "platform"]},'
    '{"id": 2, "name": "Bob Jones", "email": "bob@example.com",'
    ' "department": "Marketing", "active": true, "role": "user",'
    ' "last_login": "2026-04-11T14:22:00Z", "projects": ["campaigns"]},'
    '{"id": 3, "name": "Carol White", "email": "carol@example.com",'
    ' "department": "Engineering", "active": true, "role": "manager",'
    ' "last_login": "2026-04-12T08:45:00Z", "projects": ["platform", "api"]},'
    '{"id": 4, "name": "David Brown", "email": "david@example.com",'
    ' "department": "Sales", "active": true, "role": "user",'
    ' "last_login": "2026-04-09T16:30:00Z", "projects": ["pipeline"]},'
    '{"id": 5, "name": "Eva Green", "email": "eva@example.com",'
    ' "department": "Engineering", "active": true, "role": "user",'
    ' "last_login": "2026-04-13T11:00:00Z", "projects": ["infra"]},'
    '{"id": 6, "name": "Frank Lee", "email": "frank@example.com",'
    ' "department": "HR", "active": true, "role": "user",'
    ' "last_login": "2026-04-08T10:15:00Z", "projects": []}'
    '], "total": 6, "query": "SELECT * FROM users WHERE active = true",'
    ' "query_stats": {"rows_scanned": 6, "execution_time_ms": 89,'
    ' "index_used": "idx_users_active", "cache_hit": false}}'
)

# Small follow-up result — stays under the firewall threshold.
SMALL_DB_RESULT = (
    '{"count": 3, "department": "Engineering",'
    ' "users": ["Alice Smith", "Carol White", "Eva Green"]}'
)

estimator = CharDivFourEstimator()


def mock_llm(prompt: str) -> str:
    """Deterministic mock LLM — no API key required.

    Simulates an LLM that picks a tool and generates a response based on
    the phase keyword present in *prompt*.  Same input always produces the
    same output, consistent with the project's determinism convention.

    Args:
        prompt: The input prompt passed to the (mock) model.

    Returns:
        A deterministic response string.
    """
    if "route" in prompt.lower():
        return "I'll use the search_database tool."
    if "call" in prompt.lower():
        return '{"query": "SELECT * FROM users WHERE active = true"}'
    return "Based on the results, there are 6 active users, 3 in Engineering."


def _format_history(messages: list[BaseMessage]) -> str:
    """Format a LangChain message list as a plain concatenated prompt string.

    Args:
        messages: List of LangChain ``BaseMessage`` instances.

    Returns:
        Double-newline-separated ``Role: content`` lines.
    """
    parts: list[str] = []
    for msg in messages:
        role = type(msg).__name__.replace("Message", "")
        parts.append(f"{role}: {msg.content}")
    return "\n\n".join(parts)


def without_contextweaver() -> dict[str, int]:
    """Naive memory via LangChain ``InMemoryChatMessageHistory``.

    Measures the token count of the full growing history at each of the
    four phase-equivalent boundaries.  No budget is applied — every prior
    message is appended verbatim, including the large tool result.

    Returns:
        Mapping of phase name → estimated token count at that boundary.
    """
    history = InMemoryChatMessageHistory()
    tokens: dict[str, int] = {}

    # Turn 1 — user asks about active users.
    history.add_message(HumanMessage(content="How many active users do we have?"))
    tokens["route"] = estimator.estimate(_format_history(list(history.messages)))

    history.add_message(AIMessage(content=mock_llm("route: active users")))
    tokens["call"] = estimator.estimate(_format_history(list(history.messages)))

    # Large tool result — stored verbatim; no firewall in naive memory.
    history.add_message(ToolMessage(content=LARGE_DB_RESULT, tool_call_id="tc1"))
    tokens["interpret"] = estimator.estimate(_format_history(list(history.messages)))

    # Turn 2 — user follows up; full history grows further.
    history.add_message(AIMessage(content="Found 6 active users. What would you like to know?"))
    history.add_message(HumanMessage(content="Which ones are in Engineering?"))
    history.add_message(AIMessage(content=mock_llm("route: Engineering department")))
    history.add_message(ToolMessage(content=SMALL_DB_RESULT, tool_call_id="tc2"))
    tokens["answer"] = estimator.estimate(_format_history(list(history.messages)))

    return tokens


def with_contextweaver() -> tuple[dict[str, int], int, int, int]:
    """Phase-aware context compilation with firewall and dependency closure.

    Builds a ``ContextPack`` at each of the four phases using
    ``ContextManager`` with per-phase token budgets.  The large tool result
    is intercepted by the context firewall; only a summary appears in the
    prompt and the raw payload is stored in the artifact store.

    Returns:
        A 4-tuple of (per-phase token-count dict, included_count,
        dropped_count, dependency_closures) from the answer-phase
        ``BuildStats``.
    """
    budget = ContextBudget(route=300, call=600, interpret=500, answer=1500)
    mgr = ContextManager(budget=budget)
    tokens: dict[str, int] = {}

    # Turn 1 — user asks about active users.
    mgr.ingest(
        ContextItem(id="u1", kind=ItemKind.user_turn, text="How many active users do we have?")
    )
    route_pack = mgr.build_sync(phase=Phase.route, query="active users")
    tokens["route"] = estimator.estimate(route_pack.prompt)

    # Agent routes to search_database; mock LLM produces the decision.
    agent_decision = mock_llm("route: How many active users do we have?")
    mgr.ingest(ContextItem(id="a0", kind=ItemKind.agent_msg, text=agent_decision))
    mgr.ingest(
        ContextItem(
            id="tc1",
            kind=ItemKind.tool_call,
            text='search_database(query="SELECT * FROM users WHERE active = true")',
            parent_id="u1",
        )
    )
    call_pack = mgr.build_sync(phase=Phase.call, query="active users database query")
    tokens["call"] = estimator.estimate(call_pack.prompt)

    # Large tool result — firewall summarises; raw bytes go to the artifact store.
    mgr.ingest_tool_result(
        tool_call_id="tc1",
        raw_output=LARGE_DB_RESULT,
        tool_name="search_database",
        firewall_threshold=200,
    )
    interpret_pack = mgr.build_sync(phase=Phase.interpret, query="active users count")
    tokens["interpret"] = estimator.estimate(interpret_pack.prompt)

    # Turn 2 — user follows up; dependency closure keeps tc2 ↔ tr2 together.
    mgr.ingest(
        ContextItem(
            id="a1",
            kind=ItemKind.agent_msg,
            text="Found 6 active users. What would you like to know?",
        )
    )
    mgr.ingest(ContextItem(id="u2", kind=ItemKind.user_turn, text="Which ones are in Engineering?"))
    agent_decision_2 = mock_llm("route: Engineering department users")
    mgr.ingest(ContextItem(id="a2", kind=ItemKind.agent_msg, text=agent_decision_2))
    mgr.ingest(
        ContextItem(
            id="tc2",
            kind=ItemKind.tool_call,
            text=(
                "search_database(query=\"SELECT * FROM users WHERE department = 'Engineering'\")"
            ),
            parent_id="u2",
        )
    )
    mgr.ingest_tool_result(
        tool_call_id="tc2",
        raw_output=SMALL_DB_RESULT,
        tool_name="search_database",
        firewall_threshold=200,
    )
    answer_pack = mgr.build_sync(phase=Phase.answer, query="engineering active users")
    tokens["answer"] = estimator.estimate(answer_pack.prompt)

    stats = answer_pack.stats
    return tokens, stats.included_count, stats.dropped_count, stats.dependency_closures


def main() -> None:
    """Print a side-by-side comparison of naive LangChain memory vs contextweaver."""
    if not _LANGCHAIN_AVAILABLE:  # pragma: no cover
        print(
            "langchain-core is not installed — skipping demo.\n"
            "Install with:  pip install -e '.[langchain]'"
        )
        return
    print("=" * 70)
    print("contextweaver — LangChain Memory Replacement Demo")
    print("Replacing InMemoryChatMessageHistory with phase-specific budgets")
    print("=" * 70)

    without_tokens = without_contextweaver()
    with_tokens, included, dropped, closures = with_contextweaver()

    # WITHOUT
    print("\nWITHOUT contextweaver  (LangChain InMemoryChatMessageHistory)")
    print("─" * 60)
    verbatim_phases = {"interpret", "answer"}
    for phase_name, tok in without_tokens.items():
        note = "  ← large result included verbatim" if phase_name in verbatim_phases else ""
        print(f"  {phase_name:<12} {tok:>6,} tokens{note}")

    # WITH
    # Route/call phases may exceed the naive count: contextweaver compiles a richer,
    # phase-specific context (tool schemas, agent decisions, dependency closure).
    # The reduction is most pronounced at interpret/answer where the large tool
    # result dominates — that is the intended headline.
    phase_budgets = {"route": 300, "call": 600, "interpret": 500, "answer": 1500}
    print("\nWITH contextweaver  (phase-specific budgets + context firewall)")
    print("─" * 60)
    for phase_name, tok in with_tokens.items():
        cap = phase_budgets[phase_name]
        status = "within budget" if tok <= cap else "over budget"
        naive = without_tokens[phase_name]
        note = "  ← richer prompt (tool schema + context items)" if tok > naive else ""
        print(f"  {phase_name:<12} {tok:>6,} tokens  (limit: {cap:,})  [{status}]{note}")

    # BuildStats (answer phase)
    # dep. closures = 0 is expected here: all parent items are naturally in the
    # candidate pool for Phase.answer (all kinds allowed), so the closure pass
    # preserves pairs without needing to add extra items.
    print("\nBuildStats — answer phase")
    print("─" * 40)
    print(f"  {'items included:':22} {included}")
    print(f"  {'items dropped:':22} {dropped}")
    print(f"  {'dep. closures:':22} {closures}  (0 = all parent links already intact)")

    # Side-by-side summary
    ans_without = without_tokens["answer"]
    ans_with = with_tokens["answer"]
    reduction = (ans_without - ans_with) / ans_without * 100 if ans_without else 0.0
    print("\n" + "─" * 60)
    print(f"{'Answer-phase (LangChain):':>42} {ans_without:,} tokens")
    print(f"{'Answer-phase (contextweaver):':>42} {ans_with:,} tokens")
    print(f"{'Reduction:':>42} {reduction:.0f}%")
    print(f"{'Firewall:':>42} activated (large result summarized)")
    print(f"{'Dependency closure:':>42} {closures} pair(s) preserved")


if __name__ == "__main__":
    main()
