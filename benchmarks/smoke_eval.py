"""Optional smoke-evaluation suite for contextweaver (issue #331).

This suite inspects whether compiled context and routing remain *usable*
across a small set of fixed fixtures.  It is intentionally separate from
the deterministic benchmark scorecard (``benchmarks/benchmark.py`` /
``scripts/render_scorecard.py``) and is **not** a CI gate.

Two kinds of checks are reported separately:

- **Deterministic checks** — run with no credentials and no network.  They
  assert structural properties (expected tool in the shortlist, selected
  item metadata preserved in the rendered prompt, dependency-chain data
  still reachable after firewalling, a large result compacts to a usable
  summary, and provider-message conversion round-trips).
- **Model-dependent checks** — OFF by default.  They only run when
  ``CW_SMOKE_LLM=1`` is set, and even then skip cleanly unless a model
  adapter is wired in.  Any model-dependent output is *not* comparable
  across providers or runs and is never used for pass/fail here.

No secret values or external payloads are ever logged.

Usage::

    python benchmarks/smoke_eval.py        # deterministic checks only
    CW_SMOKE_LLM=1 python benchmarks/smoke_eval.py

Exit codes: 0 when every deterministic check passes, 1 otherwise.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from contextweaver.adapters.openai_messages import (  # noqa: E402
    from_openai_messages,
    to_openai_messages,
)
from contextweaver.config import ContextBudget  # noqa: E402
from contextweaver.context.manager import ContextManager  # noqa: E402
from contextweaver.eval.dataset import EvalDataset  # noqa: E402
from contextweaver.eval.routing import evaluate_routing  # noqa: E402
from contextweaver.protocols import CharDivFourEstimator  # noqa: E402
from contextweaver.routing.catalog import (  # noqa: E402
    generate_sample_catalog,
    load_catalog_dicts,
)
from contextweaver.routing.router import Router  # noqa: E402
from contextweaver.routing.tree import TreeBuilder  # noqa: E402
from contextweaver.store.event_log import InMemoryEventLog  # noqa: E402
from contextweaver.types import ContextItem, ItemKind, Phase  # noqa: E402

_SMOKE_DIR = Path(__file__).resolve().parent / "smoke"
_KIND_MAP = {k.value: k for k in ItemKind}


@dataclass
class CheckResult:
    """Outcome of a single smoke check."""

    name: str
    passed: bool
    detail: str


# ---------------------------------------------------------------------------
# Fixture loaders
# ---------------------------------------------------------------------------


def _load_conversation(path: Path) -> list[ContextItem]:
    items: list[ContextItem] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        raw = json.loads(line)
        items.append(
            ContextItem(
                id=raw["id"],
                kind=_KIND_MAP.get(raw.get("type", ""), ItemKind.user_turn),
                text=raw.get("text", ""),
                parent_id=raw.get("parent_id"),
                metadata=raw.get("metadata", {}),
            )
        )
    return items


def _manager_from(items: list[ContextItem]) -> ContextManager:
    log = InMemoryEventLog()
    for item in items:
        log.append(item)
    return ContextManager(
        event_log=log,
        budget=ContextBudget(route=2000, call=4000, interpret=4000, answer=6000),
        estimator=CharDivFourEstimator(),
    )


# ---------------------------------------------------------------------------
# Deterministic checks
# ---------------------------------------------------------------------------


def check_routing_shortlist() -> CheckResult:
    """The expected tool appears in the top-k shortlist for every case."""
    items = load_catalog_dicts(generate_sample_catalog(n=80, seed=42))
    router = Router(TreeBuilder().build(items), items=items, top_k=5, beam_width=3)
    dataset = EvalDataset.load(_SMOKE_DIR / "routing_cases.json")
    report = evaluate_routing(router, dataset, catalog_ids={it.id for it in items})
    passed = report.top_5_recall == 1.0 and report.queries_evaluated == len(dataset)
    return CheckResult(
        "routing_shortlist",
        passed,
        f"recall@5={report.top_5_recall} over {report.queries_evaluated} cases",
    )


def check_context_metadata_preserved() -> CheckResult:
    """Selected tool-call metadata (function name + args) survives compilation."""
    items = _load_conversation(_SMOKE_DIR / "conversation.jsonl")
    mgr = _manager_from(items)
    pack = mgr.build_sync(phase=Phase.answer, query="refund payment")
    has_name = "refund_payment" in pack.prompt
    has_arg = "pay_42" in pack.prompt
    return CheckResult(
        "context_metadata_preserved",
        has_name and has_arg,
        f"function_name={has_name} arg={has_arg}",
    )


def check_dependency_chain_available() -> CheckResult:
    """Firewalled tool-result raw data stays reachable via the artifact store."""
    mgr = _manager_from([ContextItem(id="u1", kind=ItemKind.user_turn, text="look up rows")])
    raw = "ROW DATA; " * 600  # ~6 KB, above the 2 KB firewall threshold
    _item, envelope = mgr.ingest_tool_result_sync("call-1", raw, tool_name="search_invoices")
    refs = mgr.artifact_store.list_refs()
    reachable = bool(refs) and mgr.artifact_store.get(refs[0].handle).decode() == raw
    return CheckResult(
        "dependency_chain_available",
        reachable and bool(envelope.summary),
        f"artifacts={len(refs)} raw_reachable={reachable}",
    )


def check_large_result_compaction() -> CheckResult:
    """A large tool result compacts to a non-empty, much smaller summary."""
    mgr = _manager_from([ContextItem(id="u1", kind=ItemKind.user_turn, text="summarize")])
    raw = "ROW DATA; " * 600
    _item, envelope = mgr.ingest_tool_result_sync("call-1", raw, tool_name="search_invoices")
    compacted = 0 < len(envelope.summary) < len(raw)
    return CheckResult(
        "large_result_compaction",
        compacted,
        f"summary={len(envelope.summary)}B raw={len(raw)}B",
    )


def check_provider_message_roundtrip() -> CheckResult:
    """OpenAI message conversion preserves tool names and arguments."""
    messages = json.loads((_SMOKE_DIR / "openai_messages.json").read_text(encoding="utf-8"))
    restored = to_openai_messages(from_openai_messages(messages))
    return CheckResult(
        "provider_message_roundtrip",
        restored == messages,
        f"roundtrip_equal={restored == messages}",
    )


DETERMINISTIC_CHECKS: list[Callable[[], CheckResult]] = [
    check_routing_shortlist,
    check_context_metadata_preserved,
    check_dependency_chain_available,
    check_large_result_compaction,
    check_provider_message_roundtrip,
]


def run_deterministic() -> list[CheckResult]:
    """Run every deterministic check and return results."""
    return [check() for check in DETERMINISTIC_CHECKS]


def model_dependent_enabled() -> bool:
    """Whether the optional model-backed checks are explicitly enabled."""
    return os.environ.get("CW_SMOKE_LLM") == "1"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    print("contextweaver smoke evaluation (issue #331)")
    print("=" * 48)
    print("Deterministic checks (no credentials, no network):")
    results = run_deterministic()
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(f"  [{status}] {result.name}: {result.detail}")

    print()
    print("Model-dependent checks (not comparable across providers/runs):")
    if not model_dependent_enabled():
        print("  [SKIP] disabled by default; set CW_SMOKE_LLM=1 to enable")
    else:
        print("  [SKIP] no model adapter configured for this run")

    ok = all(result.passed for result in results)
    print()
    print(f"RESULT: {'OK' if ok else 'FAILED'} ({sum(r.passed for r in results)}/{len(results)})")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
