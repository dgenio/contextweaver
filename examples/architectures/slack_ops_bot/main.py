"""Slack ops bot — production reference architecture (#198).

An internal Slack bot that fronts ~48 ops tools (log search, deploy, on-call,
alerts, tickets, metrics, identity, infra, feature flags). For each user
message:

1. The :class:`Router` narrows the 48-tool catalog to a top-3 shortlist
   (route phase, ``Phase.route``).
2. The bot picks one tool from the shortlist using an explicit intent map.
   That separation is the load-bearing pattern: contextweaver bounds the
   choice; the bot (or in production, an LLM with the shortlist in its
   prompt) makes the final selection.
3. The tool is called against a mocked backend; large outputs go through
   the firewall (raw bytes to the artifact store, summary on the prompt).
4. Persistent facts (who's on-call, what just rolled back) are written via
   :meth:`ContextManager.add_fact_sync` so they survive across turns.
5. The answer-phase build assembles a budget-aware prompt for the LLM.

This is mocked: tool implementations return canned strings, no real
Slack / log backend / deploy system is touched. The point is to demonstrate
how routing, the firewall, and persistent facts compose around a realistic
Slack-shaped transcript, not to integrate with Slack.

Run standalone::

    python examples/architectures/slack_ops_bot/main.py

Or via ``make example`` (the ``architectures`` umbrella target).
"""

from __future__ import annotations

import json
from pathlib import Path

from contextweaver.config import ContextBudget
from contextweaver.context.manager import ContextManager
from contextweaver.routing.catalog import Catalog, load_catalog_yaml
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import ContextItem, ItemKind, Phase

# An evening on-call investigation: the api-gateway is erroring, the bot
# narrates the response and writes facts that subsequent turns reuse.
#
# Each entry is ``(user_text, intent)`` where ``intent`` is the tool the bot
# would pick *given the routed shortlist*. The intent map is deliberately
# explicit so the architecture demonstrates the bounded-choice pattern (the
# Router shortlist contains the right tool; the bot decides which to call).
TRANSCRIPT: list[tuple[str, str]] = [
    ("look up the on-call engineer for api-gateway", "oncall.lookup"),
    ("tail the last hour of api-gateway logs", "logs.tail"),
    ("show api-gateway deploy status", "deploy.status"),
    ("roll back the api-gateway deploy to the previous build", "deploy.rollback"),
    ("create a new incident ticket for this api-gateway outage", "tickets.create"),
    ("show me the on-call schedule for tomorrow", "oncall.schedule"),
]


# Canned tool results. The api-gateway log dump is intentionally large so the
# firewall kicks in (>2000 chars) and the prompt only sees a compact summary.
_LARGE_LOG_DUMP = json.dumps(
    {
        "service": "api-gateway",
        "window": "2026-05-15T18:00Z..2026-05-15T19:00Z",
        "total_events": 240,
        "errors": 47,
        "warnings": 12,
        "events": [
            {
                "ts": f"2026-05-15T18:{i // 5:02d}:{(i % 5) * 12:02d}Z",
                "level": "ERROR" if i % 5 == 0 else "INFO",
                "msg": (
                    f"upstream timeout against payments-svc after {120 + (i % 60)}ms"
                    if i % 5 == 0
                    else f"request {i} handled in {15 + (i % 30)}ms"
                ),
                "trace_id": f"trace-{i:04d}",
                "deploy_sha": "9f12abc" if i < 180 else "8a01def",
            }
            for i in range(240)
        ],
    },
    indent=None,
)

_TOOL_RESPONSES: dict[str, str] = {
    "oncall.lookup": "primary on-call for api-gateway: alice@example.com (US/PT)",
    "logs.tail": _LARGE_LOG_DUMP,
    "deploy.status": (
        "api-gateway deploy status:\n"
        "  current   = 9f12abc (live since 2026-05-15T17:42Z)\n"
        "  previous  = 8a01def (replaced at 2026-05-15T17:42Z)\n"
        "  health    = degraded (47 errors in the last hour)\n"
    ),
    "deploy.rollback": ("deploy.rollback ok — api-gateway reverted from 9f12abc to 8a01def"),
    "tickets.create": "ticket OPS-4821 opened; linked to deploy 9f12abc",
    "oncall.schedule": (
        "on-call schedule (next 24h):\n"
        "  2026-05-15 18:00..2026-05-16 02:00 — alice@example.com\n"
        "  2026-05-16 02:00..2026-05-16 10:00 — bob@example.com\n"
        "  2026-05-16 10:00..2026-05-16 18:00 — carol@example.com\n"
    ),
}

CATALOG_PATH = Path(__file__).parent / "catalog.yaml"


def _print_header(title: str) -> None:
    """Print a section banner consistent with the other example scripts."""
    print()
    print("=" * 76)
    print(title)
    print("=" * 76)


def _build_router(catalog: Catalog) -> Router:
    """Compile the catalog into a routing graph and wrap it in a Router."""
    items = catalog.all()
    graph = TreeBuilder(max_children=8).build(items)
    # top_k=3 so the bot has a shortlist to pick from, not just the top-1.
    return Router(graph, items=items, top_k=3)


def _select_from_shortlist(shortlist: list[str], intent: str) -> str:
    """Pick *intent* if it is in *shortlist*, else fall back to shortlist[0].

    Real-world bots (or LLMs) make this decision against the shortlist; the
    point of the architecture is that contextweaver bounds the choice to a
    handful of options, not that it executes the choice for you.
    """
    if intent in shortlist:
        return intent
    return shortlist[0]


def main() -> None:
    """Run the Slack ops bot scenario end-to-end."""
    _print_header("contextweaver -- Slack ops bot reference architecture")

    catalog = Catalog()
    for item in load_catalog_yaml(CATALOG_PATH):
        catalog.register(item)
    print(f"Loaded catalog: {len(catalog.all())} tools from {CATALOG_PATH.name}")

    router = _build_router(catalog)
    # Tight budgets to make the firewall and dependency closure load-bearing.
    budget = ContextBudget(route=1500, call=2000, interpret=3000, answer=4000)
    mgr = ContextManager(budget=budget)

    intent_match_count = 0

    # ------------------------------------------------------------------
    # Turn-by-turn investigation.
    # ------------------------------------------------------------------
    for turn_idx, (user_text, intent) in enumerate(TRANSCRIPT, start=1):
        _print_header(f"Turn {turn_idx}")
        print(f"user:     {user_text}")

        u_id = f"u{turn_idx}"
        mgr.ingest_sync(ContextItem(id=u_id, kind=ItemKind.user_turn, text=user_text))

        # Routing — bounded shortlist of 3. Never sees full schemas.
        result = router.route(user_text)
        shortlist = result.candidate_ids
        chosen = _select_from_shortlist(shortlist, intent)
        intent_in_shortlist = intent in shortlist
        if intent_in_shortlist:
            intent_match_count += 1
        print(f"routed:   {shortlist}")
        print(
            f"chosen:   {chosen}  "
            f"(intent={intent!r}, "
            f"{'in shortlist' if intent_in_shortlist else 'NOT in shortlist'})"
        )

        route_pack = mgr.build_sync(phase=Phase.route, query=user_text)
        route_tokens = sum(route_pack.stats.tokens_per_section.values())
        print(f"route prompt: {route_pack.stats.included_count} items / {route_tokens} tokens")

        # Tool call + (mocked) result. Firewall fires when the result is large.
        tc_id = f"tc{turn_idx}"
        mgr.ingest_sync(
            ContextItem(
                id=tc_id,
                kind=ItemKind.tool_call,
                text=f"{chosen}(...)",
                parent_id=u_id,
            )
        )
        raw_output = _TOOL_RESPONSES.get(chosen, f"{chosen} returned ok")
        item, envelope = mgr.ingest_tool_result_sync(
            tool_call_id=tc_id,
            raw_output=raw_output,
            tool_name=chosen,
            firewall_threshold=2000,
        )
        if item.artifact_ref is not None and len(raw_output) > 2000:
            print(
                f"firewall: {len(raw_output):,} chars -> "
                f"{len(item.text):,}-char summary "
                f"(artifact {item.artifact_ref.handle})"
            )

        # Persistent facts that should survive across turns.
        if chosen == "oncall.lookup":
            mgr.add_fact_sync(
                key="oncall.api-gateway",
                value="alice@example.com",
                metadata={"source": chosen, "turn": str(turn_idx)},
            )
        elif chosen == "deploy.rollback":
            mgr.add_fact_sync(
                key="deploy.api-gateway",
                value="rolled back from 9f12abc to 8a01def",
                metadata={"source": chosen, "turn": str(turn_idx)},
            )
        elif chosen == "tickets.create":
            mgr.add_fact_sync(
                key="incident.api-gateway",
                value="OPS-4821",
                metadata={"source": chosen, "turn": str(turn_idx)},
            )

        # Answer-phase build for this turn — visible budget pressure if it shows up.
        answer = mgr.build_sync(phase=Phase.answer, query=user_text)
        ans_tokens = sum(answer.stats.tokens_per_section.values())
        print(
            f"answer prompt: included={answer.stats.included_count}  "
            f"dropped={answer.stats.dropped_count}  "
            f"dedup={answer.stats.dedup_removed}  "
            f"closures={answer.stats.dependency_closures}  "
            f"tokens={ans_tokens}"
        )

    # ------------------------------------------------------------------
    # Summary: persisted facts, the final prompt, and the routing scoreboard.
    # ------------------------------------------------------------------
    _print_header("Persisted facts (carry across turns)")
    for fact in sorted(mgr.fact_store.all(), key=lambda f: f.key):
        print(f"  {fact.key} = {fact.value}")

    _print_header("Final answer-phase prompt")
    final = mgr.build_sync(phase=Phase.answer, query=TRANSCRIPT[-1][0])
    print(final.prompt)
    print()
    print("--- BuildStats ---")
    print(f"total_candidates:    {final.stats.total_candidates}")
    print(f"included_count:      {final.stats.included_count}")
    print(f"dropped_count:       {final.stats.dropped_count}")
    print(f"dedup_removed:       {final.stats.dedup_removed}")
    print(f"dependency_closures: {final.stats.dependency_closures}")
    print(f"tokens_per_section:  {final.stats.tokens_per_section}")

    _print_header("Routing scoreboard")
    print(
        f"intent in router top-3: {intent_match_count}/{len(TRANSCRIPT)}  "
        f"({intent_match_count * 100 // len(TRANSCRIPT)}%)"
    )
    print(
        "Default scorer backend is TF-IDF. If your domain's tool names share "
        "vocabulary (e.g. lookup), try Router(scorer_backend='bm25' | 'fuzzy')."
    )


if __name__ == "__main__":
    main()
