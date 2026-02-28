"""Minimal agent loop example.

Demonstrates the basic contextweaver usage pattern: append events to the
event log, build a context pack, and print the resulting prompt.
"""

from __future__ import annotations

from contextweaver.context.manager import ContextManager
from contextweaver.store.event_log import InMemoryEventLog
from contextweaver.types import ContextItem, ItemKind, Phase


def main() -> None:
    log = InMemoryEventLog()
    log.append(
        ContextItem(id="u1", kind=ItemKind.user_turn, text="How many rows are in the users table?")
    )
    log.append(
        ContextItem(id="a1", kind=ItemKind.agent_msg, text="I'll check the database for you.")
    )
    log.append(
        ContextItem(
            id="tc1",
            kind=ItemKind.tool_call,
            text='db_query(sql="SELECT COUNT(*) FROM users")',
            parent_id="u1",
        )
    )
    log.append(
        ContextItem(id="tr1", kind=ItemKind.tool_result, text="count: 1042", parent_id="tc1")
    )

    mgr = ContextManager(event_log=log)
    pack = mgr.build_sync(phase=Phase.answer, query="users table row count")

    print("=== Compiled Context ===")
    print(pack.prompt)
    print("\n=== Stats ===")
    print(f"Total candidates: {pack.stats.total_candidates}")
    print(f"Included: {pack.stats.included_count}")
    print(f"Dedup removed: {pack.stats.dedup_removed}")


if __name__ == "__main__":
    main()
