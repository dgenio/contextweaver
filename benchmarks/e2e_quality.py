"""Public end-to-end quality + cost benchmark vs a *competent* baseline (issue #345).

Every other committed benchmark measures token reduction and routing recall
against a *naive* "concatenate all schemas + full history" baseline.  The
fair skeptic's objection is: *"Nobody dumps everything — I already truncate
history and pass relevant tools.  What does this save me versus a competently
built agent, and does answer quality survive?"*

This harness answers that by running the **same realistic tool-using tasks
three ways** and scoring quality *and* cost for each:

1. ``naive``      — every tool schema + full history in the prompt.
2. ``competent``  — a hand-built baseline: truncated history + a namespace/
   keyword-shortlisted tool set (what a careful engineer writes by hand).
3. ``contextweaver`` — Router-shortlisted tools + a budgeted ContextManager
   build.

Quality metrics (per strategy): tool-selection accuracy, hallucinated-tool
rate, end-task answer accuracy.  Cost metrics: prompt tokens and estimated
input cost.  The headline #345 wants is of the form *"equal-or-better answer
quality at N% lower cost vs the competent baseline,"* not "fewer tokens vs
naive."

Model access, two ways (mirrors ``benchmarks/smoke_eval.py`` and the
``LlmSummarizer`` plugin — no LLM SDK dependency):

- **Deterministic stub model (default).**  A dependency-free, credential-free
  responder that selects from *only the tools present in the prompt*, exactly
  as a real model would.  It exercises the full harness — prompt construction,
  token/cost accounting, accuracy + hallucination scoring, report rendering —
  so the pipeline is testable in CI.  Stub numbers are **illustrative
  mechanics only**; they are not the published headline.
- **Real model (opt-in).**  Set ``CW_E2E_LLM=1`` and provide a
  ``call_fn(prompt: str) -> str`` (see :func:`run`).  The published quality+cost
  headline must come from such a run, with the model id and date pinned in the
  committed report.  Without a wired adapter the real path skips cleanly.

Usage::

    python benchmarks/e2e_quality.py                       # stub model, prints a scorecard
    python benchmarks/e2e_quality.py --output benchmarks/results/e2e_quality.json
    CW_E2E_LLM=1 python benchmarks/e2e_quality.py          # opt-in real-model path

Exit codes: 0 on success, 1 on error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from contextweaver._utils import jaccard, tokenize  # noqa: E402
from contextweaver.config import ContextBudget  # noqa: E402
from contextweaver.context.manager import ContextManager  # noqa: E402
from contextweaver.protocols import CharDivFourEstimator  # noqa: E402
from contextweaver.routing.catalog import (  # noqa: E402
    generate_sample_catalog,
    load_catalog_dicts,
)
from contextweaver.routing.router import Router  # noqa: E402
from contextweaver.routing.tree import TreeBuilder  # noqa: E402
from contextweaver.store.event_log import InMemoryEventLog  # noqa: E402
from contextweaver.types import ContextItem, ItemKind, Phase, SelectableItem  # noqa: E402

_E2E_DIR = Path(__file__).resolve().parent / "e2e"
_TASKS_PATH = _E2E_DIR / "tasks.json"

# Illustrative input price (USD per million prompt tokens). Cost is a linear
# function of prompt tokens, so the *relative* cost ranking is independent of
# this constant; override with --price for a specific model/date in the report.
_DEFAULT_PRICE_PER_MTOK = 3.0

# Competent-baseline knobs: how a careful engineer would prune by hand.
_COMPETENT_TOOL_CAP = 12
_COMPETENT_HISTORY_TURNS = 6
_CW_TOP_K = 8

_TOOL_ID_RE = re.compile(r"^([a-z0-9_]+(?:\.[a-z0-9_]+)+) —", re.MULTILINE)
_QUERY_RE = re.compile(r"^User request: (.+)$", re.MULTILINE)


@dataclass
class Task:
    """One tool-using task with its gold tool and an answer-correctness probe."""

    query: str
    expected_tool: str
    answer_contains: str

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Task:
        return cls(
            query=str(data["query"]),
            expected_tool=str(data["expected_tool"]),
            answer_contains=str(data["answer_contains"]),
        )


@dataclass
class StrategyResult:
    """Aggregated quality + cost metrics for one prompt-construction strategy."""

    strategy: str
    tasks_evaluated: int
    tool_accuracy: float
    hallucination_rate: float
    answer_accuracy: float
    avg_prompt_tokens: float
    total_prompt_tokens: int
    est_cost_usd: float


@dataclass
class E2EReport:
    """Full three-way comparison plus run provenance."""

    model: str
    price_per_mtok: float
    results: list[StrategyResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "price_per_mtok": self.price_per_mtok,
            "results": [asdict(r) for r in self.results],
        }


# ---------------------------------------------------------------------------
# Model contract
# ---------------------------------------------------------------------------

# A model is any ``call_fn(prompt) -> raw_text``; the harness instructs the
# model (in the prompt) to answer with JSON ``{"tool": ..., "answer": ...}``.
CallFn = Callable[[str], str]


@dataclass
class ModelResponse:
    chosen_tool: str | None
    answer: str


def _parse_response(raw: str) -> ModelResponse:
    """Best-effort parse of a model's JSON action; tolerant of surrounding text."""
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(raw[start : end + 1])
            tool = obj.get("tool")
            return ModelResponse(
                chosen_tool=str(tool) if tool else None,
                answer=str(obj.get("answer", "")),
            )
        except (json.JSONDecodeError, AttributeError):
            pass
    return ModelResponse(chosen_tool=None, answer=raw.strip())


def stub_call_fn(prompt: str) -> str:
    """Deterministic, credential-free stand-in for a real model.

    Selects from *only the tool ids present in the prompt* (exactly what a
    real model sees), choosing the one whose ``id + description`` best matches
    the user request by token overlap.  Used for CI/testing and as the default
    so the harness runs anywhere; its numbers are illustrative, not published.
    """
    query_match = _QUERY_RE.search(prompt)
    query = query_match.group(1) if query_match else ""
    q_tokens = tokenize(query)

    offered: list[str] = _TOOL_ID_RE.findall(prompt)
    descriptions = dict(_iter_offered(prompt))

    best_id: str | None = None
    best_score = -1.0
    for tid in offered:
        score = jaccard(q_tokens, tokenize(f"{tid} {descriptions.get(tid, '')}"))
        # Deterministic tie-break by id keeps runs reproducible.
        if score > best_score or (score == best_score and (best_id is None or tid < best_id)):
            best_score, best_id = score, tid

    answer = f"Handled the request using {best_id}." if best_id else "No suitable tool found."
    return json.dumps({"tool": best_id, "answer": answer})


def _iter_offered(prompt: str) -> list[tuple[str, str]]:
    """Extract ``(tool_id, description)`` pairs from the prompt's tool block."""
    pairs: list[tuple[str, str]] = []
    for line in prompt.splitlines():
        if " — " in line:
            head, _, desc = line.partition(" — ")
            tid = head.strip()
            if _TOOL_ID_RE.match(f"{tid} —"):
                pairs.append((tid, desc.strip()))
    return pairs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def load_tasks(path: Path = _TASKS_PATH) -> list[Task]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [Task.from_dict(entry) for entry in raw]


def _build_catalog(n: int = 80, seed: int = 42) -> list[SelectableItem]:
    return sorted(load_catalog_dicts(generate_sample_catalog(n=n, seed=seed)), key=lambda i: i.id)


def _synthetic_history(turns: int = 14) -> list[ContextItem]:
    """A deterministic prior conversation, long enough that truncation matters."""
    items: list[ContextItem] = []
    for i in range(turns):
        kind = ItemKind.user_turn if i % 2 == 0 else ItemKind.agent_msg
        items.append(
            ContextItem(
                id=f"h{i:02d}",
                kind=kind,
                text=f"Earlier turn {i}: discussing account setup and billing details.",
            )
        )
    return items


# ---------------------------------------------------------------------------
# Prompt construction strategies
# ---------------------------------------------------------------------------

_PREAMBLE = (
    "You are a tool-using assistant. Choose exactly one tool.\n"
    'Respond ONLY with JSON: {"tool": "<tool_id or null>", "answer": "<short answer>"}\n'
)


def _render_tools(items: list[SelectableItem]) -> str:
    return "Available tools:\n" + "\n".join(f"{it.id} — {it.description}" for it in items)


def _render_history(items: list[ContextItem]) -> str:
    return "Conversation so far:\n" + "\n".join(f"- {it.text}" for it in items)


def build_naive_prompt(
    task: Task, catalog: list[SelectableItem], history: list[ContextItem]
) -> str:
    """Every tool + the full history — the strawman everyone already beats."""
    return (
        f"{_PREAMBLE}\n{_render_history(history)}\n\n"
        f"{_render_tools(catalog)}\n\nUser request: {task.query}"
    )


def build_competent_prompt(
    task: Task, catalog: list[SelectableItem], history: list[ContextItem]
) -> str:
    """A careful hand-built baseline: truncated history + keyword-shortlisted tools."""
    q_tokens = tokenize(task.query)
    ranked = sorted(
        catalog,
        key=lambda it: (
            -jaccard(q_tokens, tokenize(f"{it.id} {it.name} {it.description}")),
            it.id,
        ),
    )
    shortlist = ranked[:_COMPETENT_TOOL_CAP]
    recent = history[-_COMPETENT_HISTORY_TURNS:]
    return (
        f"{_PREAMBLE}\n{_render_history(recent)}\n\n"
        f"{_render_tools(shortlist)}\n\nUser request: {task.query}"
    )


def build_contextweaver_prompt(
    task: Task,
    catalog: list[SelectableItem],
    history: list[ContextItem],
    router: Router,
) -> str:
    """Router-shortlisted tools + a budgeted ContextManager history build."""
    result = router.route(task.query)
    by_id = {it.id: it for it in catalog}
    shortlist = [by_id[cid] for cid in result.candidate_ids[:_CW_TOP_K] if cid in by_id]

    log = InMemoryEventLog()
    for item in history:
        log.append(item)
    mgr = ContextManager(
        event_log=log,
        budget=ContextBudget(route=2000, call=4000, interpret=4000, answer=6000),
        estimator=CharDivFourEstimator(),
    )
    pack = mgr.build_sync(phase=Phase.answer, query=task.query)
    history_block = "Conversation so far:\n" + pack.prompt
    return (
        f"{_PREAMBLE}\n{history_block}\n\n{_render_tools(shortlist)}\n\nUser request: {task.query}"
    )


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _score_strategy(
    strategy: str,
    prompts: list[str],
    tasks: list[Task],
    call_fn: CallFn,
    catalog_ids: set[str],
    price_per_mtok: float,
) -> StrategyResult:
    estimator = CharDivFourEstimator()
    tool_hits = answer_hits = hallucinations = 0
    total_tokens = 0

    for prompt, task in zip(prompts, tasks, strict=True):
        total_tokens += estimator.estimate(prompt)
        response = _parse_response(call_fn(prompt))
        if response.chosen_tool == task.expected_tool:
            tool_hits += 1
        if response.chosen_tool is not None and response.chosen_tool not in catalog_ids:
            hallucinations += 1
        if task.answer_contains.lower() in response.answer.lower():
            answer_hits += 1

    n = len(tasks)
    return StrategyResult(
        strategy=strategy,
        tasks_evaluated=n,
        tool_accuracy=round(tool_hits / n, 4) if n else 0.0,
        hallucination_rate=round(hallucinations / n, 4) if n else 0.0,
        answer_accuracy=round(answer_hits / n, 4) if n else 0.0,
        avg_prompt_tokens=round(total_tokens / n, 2) if n else 0.0,
        total_prompt_tokens=total_tokens,
        est_cost_usd=round(total_tokens / 1_000_000 * price_per_mtok, 6),
    )


def run(
    call_fn: CallFn = stub_call_fn,
    *,
    model: str = "stub",
    price_per_mtok: float = _DEFAULT_PRICE_PER_MTOK,
    tasks: list[Task] | None = None,
) -> E2EReport:
    """Run all three strategies over every task and return a comparison report.

    Args:
        call_fn: ``prompt -> raw_text`` model callable. Defaults to the
            deterministic stub. Wire your own to benchmark a real model.
        model: Identifier recorded in the report (pin model+date for real runs).
        price_per_mtok: Illustrative input price; only scales absolute cost.
        tasks: Override task set (defaults to ``benchmarks/e2e/tasks.json``).
    """
    tasks = tasks if tasks is not None else load_tasks()
    catalog = _build_catalog()
    catalog_ids = {it.id for it in catalog}
    history = _synthetic_history()
    router = Router(TreeBuilder().build(catalog), items=catalog, top_k=_CW_TOP_K, beam_width=3)

    builders: dict[str, list[str]] = {
        "naive": [build_naive_prompt(t, catalog, history) for t in tasks],
        "competent": [build_competent_prompt(t, catalog, history) for t in tasks],
        "contextweaver": [build_contextweaver_prompt(t, catalog, history, router) for t in tasks],
    }

    report = E2EReport(model=model, price_per_mtok=price_per_mtok)
    for strategy, prompts in builders.items():
        report.results.append(
            _score_strategy(strategy, prompts, tasks, call_fn, catalog_ids, price_per_mtok)
        )
    return report


# ---------------------------------------------------------------------------
# Rendering / entry point
# ---------------------------------------------------------------------------


def render_scorecard(report: E2EReport) -> str:
    lines = [
        f"contextweaver e2e quality + cost (issue #345)  model={report.model}",
        "=" * 78,
        f"{'strategy':<14} {'tool_acc':>8} {'halluc':>7} {'ans_acc':>8} "
        f"{'avg_tok':>8} {'cost_usd':>9}",
    ]
    competent = next((r for r in report.results if r.strategy == "competent"), None)
    cw = next((r for r in report.results if r.strategy == "contextweaver"), None)
    for r in report.results:
        lines.append(
            f"{r.strategy:<14} {r.tool_accuracy:>8.3f} {r.hallucination_rate:>7.3f} "
            f"{r.answer_accuracy:>8.3f} {r.avg_prompt_tokens:>8.1f} {r.est_cost_usd:>9.6f}"
        )
    if competent and cw and competent.est_cost_usd > 0:
        delta = 100.0 * (1.0 - cw.est_cost_usd / competent.est_cost_usd)
        lines += [
            "",
            f"contextweaver vs competent baseline: {delta:+.1f}% cost, "
            f"tool_acc {cw.tool_accuracy:.3f} vs {competent.tool_accuracy:.3f}, "
            f"answer_acc {cw.answer_accuracy:.3f} vs {competent.answer_accuracy:.3f}",
        ]
    if report.model == "stub":
        lines += ["", "NOTE: stub model — illustrative mechanics only. Set CW_E2E_LLM=1 "]
        lines[-1] += "and wire a real call_fn for a publishable headline."
    return "\n".join(lines)


def real_model_enabled() -> bool:
    """Whether the opt-in real-model path is explicitly requested."""
    return os.environ.get("CW_E2E_LLM") == "1"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="contextweaver e2e quality + cost benchmark")
    parser.add_argument("--output", type=Path, default=None, help="Write the JSON report here")
    parser.add_argument(
        "--price", type=float, default=_DEFAULT_PRICE_PER_MTOK, help="USD per million prompt tokens"
    )
    args = parser.parse_args(argv)

    if real_model_enabled():
        # No SDK is bundled; a real run wires its own call_fn here. Until then,
        # skip cleanly rather than silently scoring the stub as if it were real.
        print("CW_E2E_LLM=1 set, but no real model adapter is wired into this run.")
        print("Import contextweaver's e2e_quality.run(call_fn=..., model=...) from your own")
        print("script to benchmark a real model, then commit the JSON report. Skipping.")
        return 0

    report = run(price_per_mtok=args.price)
    print(render_scorecard(report))
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")
        print(f"\nReport written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
