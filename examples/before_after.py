"""Before/after showpiece example.

Runs the same agent loop WITHOUT contextweaver (raw text bloats the prompt)
vs WITH contextweaver (budget-aware, firewall-protected). Prints token
counts side by side to illustrate the value of structured context management.
"""

from __future__ import annotations

from contextweaver.config import ContextBudget
from contextweaver.context.manager import ContextManager
from contextweaver.protocols import CharDivFourEstimator
from contextweaver.types import ContextItem, ItemKind, Phase

# Simulated tool results — one small, one large (exercises the firewall)
SMALL_RESULT = "status: ok\ncount: 42\npage: 1 of 5"

LARGE_RESULT = (
    "HTTP/1.1 200 OK\n"
    "Content-Type: application/json\n\n"
    '{"users": ['
    '{"id": 1, "name": "Alice Smith", "email": "alice@example.com",'
    ' "role": "admin", "department": "Engineering"},'
    '{"id": 2, "name": "Bob Jones", "email": "bob@example.com",'
    ' "role": "user", "department": "Marketing"},'
    '{"id": 3, "name": "Carol White", "email": "carol@example.com",'
    ' "role": "user", "department": "Sales"},'
    '{"id": 4, "name": "David Brown", "email": "david@example.com",'
    ' "role": "admin", "department": "Engineering"},'
    '{"id": 5, "name": "Eva Green", "email": "eva@example.com",'
    ' "role": "user", "department": "HR"},'
    '{"id": 6, "name": "Frank Lee", "email": "frank@example.com",'
    ' "role": "user", "department": "Finance"},'
    '{"id": 7, "name": "Grace Kim", "email": "grace@example.com",'
    ' "role": "manager", "department": "Engineering"},'
    '{"id": 8, "name": "Henry Wang", "email": "henry@example.com",'
    ' "role": "user", "department": "Marketing"},'
    '{"id": 9, "name": "Iris Chen", "email": "iris@example.com",'
    ' "role": "manager", "department": "Sales"},'
    '{"id": 10, "name": "Jack Park", "email": "jack@example.com",'
    ' "role": "user", "department": "Engineering"}'
    '], "total": 10, "page": 1, "pages": 1,'
    ' "query_stats": {"rows_scanned": 5842,'
    ' "execution_time_ms": 89,'
    ' "index_used": "idx_users_active",'
    ' "cache_hit": false}}'
)

estimator = CharDivFourEstimator()


def _build_events() -> list[ContextItem]:
    """Create a realistic event sequence."""
    return [
        ContextItem(id="u1", kind=ItemKind.user_turn, text="List all active users in the system"),
        ContextItem(id="a1", kind=ItemKind.agent_msg, text="I'll query the user database for you."),
        ContextItem(
            id="tc1",
            kind=ItemKind.tool_call,
            text="db_query(sql='SELECT * FROM users WHERE active=true')",
            parent_id="u1",
        ),
        ContextItem(id="tr1", kind=ItemKind.tool_result, text=LARGE_RESULT, parent_id="tc1"),
        ContextItem(
            id="a2",
            kind=ItemKind.agent_msg,
            text="Found 10 active users. What would you like to know?",
        ),
        ContextItem(id="u2", kind=ItemKind.user_turn, text="How many are in Engineering?"),
        ContextItem(
            id="tc2",
            kind=ItemKind.tool_call,
            text="db_query(sql='SELECT COUNT(*) FROM users WHERE dept=Engineering')",
            parent_id="u2",
        ),
        ContextItem(id="tr2", kind=ItemKind.tool_result, text=SMALL_RESULT, parent_id="tc2"),
    ]


def without_contextweaver() -> int:
    """Simulate a naive agent that concatenates everything."""
    events = _build_events()
    # Naive approach: just concatenate all event texts
    raw_prompt = "\n\n".join(f"[{e.kind.value}] {e.text}" for e in events)
    tokens = estimator.estimate(raw_prompt)
    return tokens


def with_contextweaver() -> tuple[int, int, int]:
    """Use contextweaver with firewall and budget control."""
    events = _build_events()
    budget = ContextBudget(answer=1500)
    mgr = ContextManager(budget=budget)

    for event in events:
        if event.kind == ItemKind.tool_result and len(event.text) > 200:
            mgr.ingest_tool_result(
                tool_call_id=event.parent_id or event.id,
                raw_output=event.text,
                tool_name="db_query",
                firewall_threshold=200,
            )
        else:
            mgr.ingest(event)

    pack = mgr.build_sync(phase=Phase.answer, query="engineering users")
    tokens = estimator.estimate(pack.prompt)
    return tokens, pack.stats.included_count, pack.stats.dropped_count


def main() -> None:
    print("=" * 60)
    print("contextweaver — Before vs After Comparison")
    print("=" * 60)

    # WITHOUT
    without_tokens = without_contextweaver()
    print(f"\n{'WITHOUT contextweaver':>30}")
    print(f"{'─' * 40}")
    print(f"{'Raw prompt tokens:':>30} {without_tokens:,}")
    print(f"{'Strategy:':>30} concatenate everything")
    print(f"{'Budget enforcement:':>30} none")
    print(f"{'Large output handling:':>30} included verbatim")

    # WITH
    with_tokens, included, dropped = with_contextweaver()
    print(f"\n{'WITH contextweaver':>30}")
    print(f"{'─' * 40}")
    print(f"{'Final prompt tokens:':>30} {with_tokens:,}")
    print(f"{'Items included:':>30} {included}")
    print(f"{'Items dropped:':>30} {dropped}")
    print(f"{'Strategy:':>30} phase-aware + firewall")
    print(f"{'Budget enforcement:':>30} 1500 tokens")
    print(f"{'Large output handling:':>30} summary + artifact ref")

    # Comparison
    reduction = ((without_tokens - with_tokens) / without_tokens * 100) if without_tokens else 0
    print(f"\n{'RESULT':>30}")
    print(f"{'─' * 40}")
    print(f"{'Token reduction:':>30} {reduction:.0f}%")
    print(f"{'Tokens saved:':>30} {without_tokens - with_tokens:,}")
    print(f"{'Budget compliance:':>30} {'Yes' if with_tokens <= 1500 else 'No'}")


if __name__ == "__main__":
    main()
