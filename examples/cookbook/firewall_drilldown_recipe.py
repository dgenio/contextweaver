"""Firewall + drilldown cookbook recipe.

Demonstrates how to handle a tool that returns a very large payload without
blowing up the prompt budget.  The context firewall intercepts the raw bytes
(stored out-of-band in the artifact store) and injects only a compact
summary into the event log.  When the agent needs more detail than the
summary provides, ``ContextManager.drilldown_sync()`` fetches a targeted
slice — by line range, JSON keys, or character head — and re-injects it as
a new ``tool_result`` so the next ``build()`` sees it.

**Important ordering note.** Each ``build()`` call replays the firewall
stage over every ``tool_result`` candidate, which re-stores the *current*
``item.text`` (a summary, post-firewall) under the artifact handle and
loses the original raw bytes.  Drill in **before** the next ``build()`` if
you need the full payload, or rely on the re-injected drilldown
``ContextItem`` carrying the slice you actually care about.

This recipe uses contextweaver core only — no framework SDK required.

Run standalone::

    python examples/cookbook/firewall_drilldown_recipe.py

Or via the project test suite::

    make example
"""

from __future__ import annotations

import json

from contextweaver.context.manager import ContextManager
from contextweaver.types import ContextItem, ItemKind, Phase

# Simulated large tool result — a JSON document with 200 log entries.  At
# ~80 chars/entry this is well above the default 2000-char firewall
# threshold, so the firewall stores the raw bytes in the artifact store and
# the prompt only sees a compact summary.
LARGE_LOG_RESULT = json.dumps(
    {
        "service": "api-gateway",
        "window": "2026-05-12T08:00Z..2026-05-12T09:00Z",
        "events": [
            {
                "ts": f"2026-05-12T08:{i // 4:02d}:{(i % 4) * 15:02d}Z",
                "level": "ERROR" if i % 17 == 0 else "INFO",
                "msg": f"request {i} handled in {15 + (i % 30)}ms",
                "trace_id": f"trace-{i:04d}",
            }
            for i in range(200)
        ],
        "total_events": 200,
        "errors": 12,
    },
    indent=None,
)


def main() -> None:
    """Run the firewall + drilldown recipe end-to-end."""
    print("=" * 70)
    print("contextweaver -- Firewall + drilldown cookbook recipe")
    print("=" * 70)
    print(
        f"\nRaw tool result size: {len(LARGE_LOG_RESULT):,} chars "
        f"({len(LARGE_LOG_RESULT.encode('utf-8')):,} bytes)"
    )

    mgr = ContextManager()

    # 1. Ingest the originating user turn and the tool call so the firewall
    # has a parent_id to attach the summarised result to.
    mgr.ingest_sync(
        ContextItem(
            id="u1", kind=ItemKind.user_turn, text="Show me the last hour of api-gateway errors."
        ),
    )
    mgr.ingest_sync(
        ContextItem(
            id="tc1",
            kind=ItemKind.tool_call,
            text="logs.fetch(service='api-gateway', window='last_hour')",
            parent_id="u1",
        ),
    )

    # 2. Ingest the raw result through the firewall.  Anything over
    # *firewall_threshold* chars is summarised; the raw bytes go to the
    # artifact store and the returned ContextItem carries an artifact_ref
    # so we can drill in later.
    item, envelope = mgr.ingest_tool_result_sync(
        tool_call_id="tc1",
        raw_output=LARGE_LOG_RESULT,
        tool_name="logs.fetch",
        firewall_threshold=2000,
    )
    assert item.artifact_ref is not None, "firewall always sets artifact_ref"
    artifact_bytes = mgr.artifact_store.get(item.artifact_ref.handle)
    print(f"\n[1] Firewall summary stored on the event log: {len(item.text):,} chars.")
    print(
        f"    Raw bytes parked in the artifact store under handle "
        f"{item.artifact_ref.handle!r}: {len(artifact_bytes):,} bytes."
    )
    print(
        f"    Envelope status: {envelope.status}, "
        f"{len(envelope.facts)} extracted facts, "
        f"{len(envelope.views)} auto-views available."
    )

    # 3. Drill in *before* the next build() — the firewall stage replays on
    # every build and would overwrite the artifact with the latest
    # (already-summarised) item.text.  Targeted slices: by character head,
    # by JSON key, and by line range.
    head_500 = mgr.drilldown_sync(
        handle=item.artifact_ref.handle,
        selector={"type": "head", "chars": 600},
    )
    print(f"\n[2] Drilldown (head, 600 chars) returned {len(head_500):,} chars.")
    print(f"    First 140: {head_500[:140]!r}…")

    keys_view = mgr.drilldown_sync(
        handle=item.artifact_ref.handle,
        selector={"type": "json_keys", "keys": ["window", "total_events", "errors"]},
    )
    print(f"\n[3] Drilldown (json_keys: window/total_events/errors): {keys_view!r}")

    # 4. Re-inject the targeted drilldown into the event log as a new
    # tool_result.  Subsequent build() calls will see this rich slice as a
    # candidate alongside the original (summarised) firewall result.
    detail = mgr.drilldown_sync(
        handle=item.artifact_ref.handle,
        selector={"type": "json_keys", "keys": ["window", "total_events", "errors"]},
        inject=True,
        parent_id="tc1",
    )
    print(
        f"\n[4] Re-injected drilldown into the event log "
        f"({len(detail):,} chars).  parent_id=tc1 keeps dependency closure intact."
    )

    # 5. Now build an interpret-phase prompt.  Both the firewall summary
    # and the injected drilldown are candidates; the budget decides which
    # to include.
    pack_interpret = mgr.build_sync(phase=Phase.interpret, query="api-gateway errors")
    total_tokens = (
        sum(pack_interpret.stats.tokens_per_section.values())
        + pack_interpret.stats.header_footer_tokens
    )
    print(
        f"\n[5] interpret-phase prompt: {len(pack_interpret.prompt):,} chars, "
        f"{total_tokens} tokens "
        f"({pack_interpret.stats.included_count} items included, "
        f"{pack_interpret.stats.dropped_count} dropped)."
    )

    # 6. Finally the answer-phase build, which the agent would send to its
    # LLM.  Dependency closure ensures the tool call and its (summarised)
    # result remain a coherent pair.
    pack_answer = mgr.build_sync(phase=Phase.answer, query="api-gateway errors")
    print(
        f"\n[6] answer-phase prompt: {len(pack_answer.prompt):,} chars "
        f"({pack_answer.stats.included_count} items included, "
        f"{pack_answer.stats.dropped_count} dropped, "
        f"{pack_answer.stats.dependency_closures} dependency closures)."
    )


if __name__ == "__main__":
    main()
