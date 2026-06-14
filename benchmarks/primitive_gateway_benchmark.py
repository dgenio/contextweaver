"""Mixed-primitive gateway benchmark: tools + resources + prompts (#673).

Quantifies the bounded-choice surface savings when the gateway shapes all three
MCP primitives (#555).  For a synthetic catalog of tools, resources, and
prompts, it compares:

- **Naive surface** — the model sees every primitive's name + description (the
  full ``tools/list`` + ``resources/list`` + ``prompts/list`` listing).
- **Gateway surface** — the model sees only the meta-tools plus a single
  bounded browse shortlist (``top_k`` ChoiceCards) per query.

It reports per-kind and overall token savings plus a quality proxy
(recall@k: did the relevant primitive appear in the browse shortlist?).

Non-gating: run via ``python benchmarks/primitive_gateway_benchmark.py``; the
companion test (``tests/test_primitive_gateway_benchmark.py``) exercises
``run_all`` on a small catalog so CI verifies the harness without gating on
absolute numbers.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from contextweaver.adapters.gateway_primitives import PrimitiveGatewayRuntime
from contextweaver.envelope import ChoiceCard
from contextweaver.routing import count_tokens

RESULTS_PATH = Path(__file__).resolve().parent / "results" / "primitive_gateway_latest.json"


class _StaticUpstream:
    """In-process :class:`PrimitiveUpstream` over fixed resource/prompt defs."""

    def __init__(self, resources: list[dict[str, Any]], prompts: list[dict[str, Any]]) -> None:
        self._resources = resources
        self._prompts = prompts

    async def list_resources(self) -> list[dict[str, Any]]:
        return list(self._resources)

    async def read_resource(self, uri: str) -> dict[str, Any]:
        return {"contents": [{"uri": uri, "text": ""}]}

    async def list_prompts(self) -> list[dict[str, Any]]:
        return list(self._prompts)

    async def get_prompt(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"messages": []}


@dataclass
class KindStats:
    """Savings for one primitive kind."""

    kind: str
    count: int
    naive_tokens: int
    browse_tokens: int
    recall_at_k: float

    @property
    def savings_pct(self) -> float:
        if self.naive_tokens == 0:
            return 0.0
        return round(100.0 * (1 - self.browse_tokens / self.naive_tokens), 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "count": self.count,
            "naive_tokens": self.naive_tokens,
            "browse_tokens": self.browse_tokens,
            "savings_pct": self.savings_pct,
            "recall_at_k": round(self.recall_at_k, 3),
        }


@dataclass
class BenchmarkReport:
    """Aggregate mixed-primitive benchmark report."""

    per_kind: list[KindStats] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        total_naive = sum(k.naive_tokens for k in self.per_kind)
        total_browse = sum(k.browse_tokens for k in self.per_kind)
        overall = round(100.0 * (1 - total_browse / total_naive), 1) if total_naive else 0.0
        return {
            "per_kind": [k.to_dict() for k in self.per_kind],
            "overall_naive_tokens": total_naive,
            "overall_browse_tokens": total_browse,
            "overall_savings_pct": overall,
        }


def _resource_defs(n: int) -> list[dict[str, Any]]:
    domains = ["docs", "logs", "config", "schema", "report"]
    return [
        {
            "uri": f"file:///{domains[i % len(domains)]}/item_{i}.md",
            "name": f"{domains[i % len(domains)]} resource {i}",
            "description": f"A {domains[i % len(domains)]} resource number {i} for the workspace.",
            "mimeType": "text/markdown",
        }
        for i in range(n)
    ]


def _prompt_defs(n: int) -> list[dict[str, Any]]:
    verbs = ["summarize", "review", "translate", "classify", "draft"]
    return [
        {
            "name": f"{verbs[i % len(verbs)]}_template_{i}",
            "description": f"Prompt to {verbs[i % len(verbs)]} content, variant {i}.",
            "arguments": [{"name": "text", "required": True}],
        }
        for i in range(n)
    ]


def _naive_tokens(defs: list[dict[str, Any]], name_key: str) -> int:
    """Tokens for the full naive listing (every entry's name + description)."""
    return sum(count_tokens(f"{d.get(name_key, '')}: {d.get('description', '')}") for d in defs)


def _browse_tokens(cards: list[ChoiceCard]) -> int:
    return sum(count_tokens(f"{c.name}: {c.description}") for c in cards)


def _recall(cards: list[ChoiceCard], expected_substr: str) -> float:
    return 1.0 if any(expected_substr in c.id or expected_substr in c.name for c in cards) else 0.0


def run_all(*, n_resources: int = 60, n_prompts: int = 40, top_k: int = 8) -> dict[str, Any]:
    """Run the mixed-primitive benchmark and return the report dict."""
    resources = _resource_defs(n_resources)
    prompts = _prompt_defs(n_prompts)
    runtime = PrimitiveGatewayRuntime(_StaticUpstream(resources, prompts), top_k=top_k)
    runtime.register_sync(resources, prompts)

    res_cards = runtime.browse_resources(query="config resource for the workspace")
    prompt_cards = runtime.browse_prompts(query="summarize content")
    res_cards = res_cards if isinstance(res_cards, list) else []
    prompt_cards = prompt_cards if isinstance(prompt_cards, list) else []

    report = BenchmarkReport(
        per_kind=[
            KindStats(
                kind="resource",
                count=len(resources),
                naive_tokens=_naive_tokens(resources, "name"),
                browse_tokens=_browse_tokens(res_cards),
                recall_at_k=_recall(res_cards, "config"),
            ),
            KindStats(
                kind="prompt",
                count=len(prompts),
                naive_tokens=_naive_tokens(prompts, "name"),
                browse_tokens=_browse_tokens(prompt_cards),
                recall_at_k=_recall(prompt_cards, "summarize"),
            ),
        ]
    )
    return report.to_dict()


def main() -> int:
    """Run the benchmark and write the JSON report."""
    report = run_all()
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    sys.stdout.write(
        "mixed-primitive gateway benchmark: "
        f"overall savings {report['overall_savings_pct']}% "
        f"({report['overall_browse_tokens']}/{report['overall_naive_tokens']} tokens)\n"
    )
    for kind in report["per_kind"]:
        sys.stdout.write(
            f"  {kind['kind']:9s} x{kind['count']:>3}: "
            f"{kind['savings_pct']:>5}% savings, recall@k={kind['recall_at_k']}\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
