"""Code-review bot — production reference architecture (#204).

A pull-request review bot fronting ~24 analysis tools (grep, git blame, git
log, lint, typecheck, test runner, plus PR review meta-tools). For each
review step:

1. The :class:`Router` narrows the 24-tool catalog to a top-3 shortlist
   (route phase, ``Phase.route``).
2. The bot picks one tool from the shortlist using an explicit intent map.
   That separation is the load-bearing pattern: contextweaver bounds the
   choice; the bot (or in production, an LLM with the shortlist in its
   prompt) makes the final selection.
3. The tool is called against a mocked backend; large outputs (the diff
   dump and the grep result) go through the firewall (raw bytes to the
   artifact store, summary on the prompt).
4. Persistent facts (target files for review, test status, lint count)
   are written via :meth:`ContextManager.add_fact_sync` so they survive
   across review steps.
5. The answer-phase build assembles a budget-aware prompt for the LLM.

The firewall is the **load-bearing pattern** here: the simulated PR diff
(~28 KB) and grep output (~6 KB) both exceed the 2 KB firewall threshold,
so the prompt only ever sees compact summaries while the raw bytes stay
addressable via the artifact store.

This is mocked: tool implementations return canned strings, no real git /
linter / type checker is invoked. The point is to demonstrate how routing,
the firewall, and persistent facts compose around a realistic
code-review-shaped transcript, not to integrate with a code host.

Run standalone::

    python examples/architectures/code_review_bot/main.py

Or via ``make example`` (the ``architectures`` umbrella target).
"""

from __future__ import annotations

import functools
import json
from pathlib import Path

from contextweaver.config import ContextBudget
from contextweaver.context.manager import ContextManager
from contextweaver.routing.catalog import Catalog, load_catalog_yaml
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import ContextItem, ItemKind, Phase

# A scripted PR-review session: the bot inspects a refactor of
# ``payments/charge.py`` that introduces a regression. Each entry is
# ``(user_text, intent)`` where ``intent`` is the tool the bot would pick
# *given the routed shortlist*. The intent map is deliberately explicit so
# the architecture demonstrates the bounded-choice pattern (the Router
# shortlist contains the right tool; the bot decides which to call).
TRANSCRIPT: list[tuple[str, str]] = [
    ("show me the diff of this pull request against main", "git.diff"),
    ("grep for the symbol legacy_charge in the codebase", "grep.symbol"),
    ("run the test suite for the changed module", "test.run_module"),
    ("run mypy on the changed module to surface type errors", "typecheck.module"),
    ("run ruff on the changed files and report style violations", "lint.run"),
    ("post a review comment requesting changes on the regression", "review.post_comment"),
]


# Canned tool results. The PR diff and grep dump are intentionally large
# so the firewall kicks in (> 2 KB) and the prompt only sees compact
# summaries.  Built lazily so importing this module (e.g. from the test
# smoke harness) does not pay the ~28 KB JSON construction cost up front.
@functools.cache
def _large_diff_dump() -> str:
    """Return a ~28 KB synthetic diff that exceeds the firewall threshold."""
    lines: list[str] = [
        "diff --git a/payments/charge.py b/payments/charge.py",
        "index 9f12abc..8a01def 100644",
        "--- a/payments/charge.py",
        "+++ b/payments/charge.py",
        "@@ -1,4 +1,4 @@",
        "-from payments.legacy_charge import charge as legacy_charge",
        "+from payments.charge_v2 import charge",
        "",
    ]
    # Pad with 200 plausible diff hunks so we cross the firewall threshold.
    for i in range(200):
        lines.append(f"@@ -{i * 4 + 10},4 +{i * 4 + 10},4 @@")
        lines.append(f"-    return legacy_charge(customer_id={i}, amount={100 + i})")
        lines.append(f"+    return charge(customer_id={i}, amount={100 + i})")
        lines.append("")
    return "\n".join(lines)


@functools.cache
def _large_grep_dump() -> str:
    """Return a ~6 KB grep result for ``legacy_charge`` across the codebase."""
    hits = [
        {
            "file": f"payments/{module}.py",
            "line": 10 + i,
            "match": f"from payments.legacy_charge import legacy_charge  # call site {i}",
        }
        for i, module in enumerate(
            [
                "charge",
                "subscription",
                "invoice",
                "refund",
                "trial",
                "promo",
                "tax",
                "reporting",
                "webhook",
                "metrics",
                "audit",
                "retry",
                "rate_limit",
                "feature_flag",
                "rollout",
                "dispute",
                "fraud",
                "limits",
                "schedule",
                "expiry",
            ]
        )
    ]
    return json.dumps({"query": "legacy_charge", "hits": hits}, indent=None)


@functools.cache
def _tool_responses() -> dict[str, str]:
    """Return the canned tool-response map. Lazy so heavy payloads only build on demand."""
    return {
        "git.diff": _large_diff_dump(),
        "grep.symbol": _large_grep_dump(),
        "test.run_module": (
            "pytest tests/test_payments_charge.py -q\n"
            "  3 passed, 2 failed in 0.42s\n"
            "  FAILED tests/test_payments_charge.py::test_legacy_charge_still_supported\n"
            "  FAILED tests/test_payments_charge.py::test_charge_decimal_precision\n"
        ),
        "typecheck.module": (
            "mypy payments/charge.py\n"
            "  payments/charge.py:7: error: Module 'payments.charge_v2' has no attribute 'charge'\n"
            "  payments/charge.py:42: error: Argument 1 has incompatible type 'int'\n"
            "  Found 2 errors in 1 file (checked 1 source file)\n"
        ),
        "lint.run": (
            "ruff check payments/charge.py\n"
            "  payments/charge.py:12:1 E402 module level import not at top of file\n"
            "  payments/charge.py:55:101 E501 line too long (108 > 100)\n"
            "  Found 2 issues\n"
        ),
        "review.post_comment": (
            "review.post_comment ok — posted inline comment on payments/charge.py:7 "
            "requesting that the legacy_charge import path be preserved"
        ),
    }


CATALOG_PATH = Path(__file__).parent / "catalog.yaml"


def _print_header(title: str) -> None:
    """Print a section banner consistent with the other architecture scripts."""
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
    """Run the code-review bot scenario end-to-end."""
    _print_header("contextweaver -- Code-review bot reference architecture")

    catalog = Catalog()
    for selectable in load_catalog_yaml(CATALOG_PATH):
        catalog.register(selectable)
    print(f"Loaded catalog: {len(catalog.all())} tools from {CATALOG_PATH.name}")

    router = _build_router(catalog)
    # Tight budgets to make the firewall load-bearing — the prompt would
    # blow these out if the diff/grep raw bytes were inlined.
    budget = ContextBudget(route=1500, call=2500, interpret=2500, answer=3500)
    mgr = ContextManager(budget=budget)

    intent_match_count = 0
    firewall_fires = 0

    # ------------------------------------------------------------------
    # Step-by-step PR review.
    # ------------------------------------------------------------------
    for turn_idx, (user_text, intent) in enumerate(TRANSCRIPT, start=1):
        _print_header(f"Step {turn_idx}")
        print(f"reviewer: {user_text}")

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
        raw_output = _tool_responses().get(chosen, f"{chosen} returned ok")
        item, _envelope = mgr.ingest_tool_result_sync(
            tool_call_id=tc_id,
            raw_output=raw_output,
            tool_name=chosen,
            firewall_threshold=2000,
        )
        if item.artifact_ref is not None and len(raw_output) > 2000:
            firewall_fires += 1
            print(
                f"firewall: {len(raw_output):,} chars -> "
                f"{len(item.text):,}-char summary "
                f"(artifact {item.artifact_ref.handle})"
            )

        # Persistent facts that should survive across review steps.
        if chosen == "git.diff":
            mgr.add_fact_sync(
                key="pr.target_file",
                value="payments/charge.py",
                metadata={"source": chosen, "step": str(turn_idx)},
            )
        elif chosen == "test.run_module":
            mgr.add_fact_sync(
                key="pr.test_status",
                value="2 failed (legacy_charge support, decimal precision)",
                metadata={"source": chosen, "step": str(turn_idx)},
            )
        elif chosen == "typecheck.module":
            mgr.add_fact_sync(
                key="pr.type_errors",
                value="2 errors (missing charge_v2.charge, int/Decimal mismatch)",
                metadata={"source": chosen, "step": str(turn_idx)},
            )

        # Answer-phase build for this step — visible budget pressure if it shows up.
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
    _print_header("Persisted facts (carry across review steps)")
    for fact in sorted(mgr.fact_store.all(), key=lambda f: f.key):
        print(f"  {fact.key} = {fact.value}")

    _print_header("Firewall scoreboard")
    print(f"firewall fires: {firewall_fires}/{len(TRANSCRIPT)}")
    print(f"artifacts kept: {len(list(mgr.artifact_store.list_refs()))}")
    print("(Each firewall fire compacts a >2 KB tool result down to a 500-char summary; ")
    print(" raw bytes stay addressable in the artifact store for drilldown.)")

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
        "vocabulary (e.g. run / check), try Router(scorer_backend='bm25' | 'fuzzy')."
    )


if __name__ == "__main__":
    main()
